# -*- coding: utf-8 -*-
import csv
import io
import json
import os
import re
from datetime import datetime, timedelta
from functools import wraps
from queue import Empty

import pymysql
from dotenv import load_dotenv
from flask import (
    Flask, Response, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from auth_service import (
    decrypt_hardware_info,
    generate_auth_code,
    verify_auth_code,
    compute_device_fingerprint,
    fingerprint_from_hardware,
)
from live_hub import live_hub

load_dotenv()

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.secret_key = os.getenv('SECRET_KEY')

try:
    from flask_sock import Sock
except ImportError:  # pragma: no cover
    Sock = None

sock = Sock(app) if Sock else None

app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')

STALE_DAYS = int(os.getenv('STALE_DAYS', '7'))
DAILY_AUTH_LIMIT_PER_DEVICE = int(os.getenv('DAILY_AUTH_LIMIT_PER_DEVICE', '50'))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('RATE_LIMIT_WINDOW_SECONDS', '60'))
RATE_LIMIT_MAX_PER_IP = int(os.getenv('RATE_LIMIT_MAX_PER_IP', '30'))
RATE_LIMIT_MAX_PER_CPU = int(os.getenv('RATE_LIMIT_MAX_PER_CPU', '20'))
AUTH_CODE_MAX_AGE_SECONDS = int(os.getenv('AUTH_CODE_MAX_AGE_SECONDS', '86400'))
LOG_RETENTION_DAYS = int(os.getenv('LOG_RETENTION_DAYS', '90'))

# 业务线 / 租户：铝水现网设备可不传，默认 lvshui；元一设备必须传 yuanyi
DEFAULT_TENANT = 'lvshui'
TENANTS = {
    'lvshui': {'code': 'lvshui', 'name': '铝水', 'tag': '铝水设备终端'},
    'yuanyi': {'code': 'yuanyi', 'name': '元一', 'tag': '元一设备终端'},
}
TENANT_ALIASES = {
    'lvshui': 'lvshui',
    'aluminum': 'lvshui',
    'lushui': 'lvshui',
    '铝水': 'lvshui',
    'yuanyi': 'yuanyi',
    'yuan yi': 'yuanyi',
    '元一': 'yuanyi',
}


def _mysql_connect(**kwargs):
    return pymysql.connect(
        host=app.config['MYSQL_HOST'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        **kwargs,
    )


def get_db():
    return _mysql_connect(database=app.config['MYSQL_DB'])


def ensure_column(cursor, table, column, definition):
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    if not cursor.fetchone():
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN {column} {definition}")


def normalize_tenant(raw, *, required=False):
    """规范化租户编码。空值默认铝水；非法值返回 None。"""
    if raw is None:
        return None if required else DEFAULT_TENANT
    text = str(raw).strip()
    if not text:
        return None if required else DEFAULT_TENANT
    code = TENANT_ALIASES.get(text.lower(), TENANT_ALIASES.get(text, text.lower()))
    return code if code in TENANTS else None


def resolve_request_tenant(*, required=False):
    """从 Header / Query / JSON 读取租户；缺省为铝水。"""
    raw = request.headers.get('X-Tenant') or request.args.get('tenant')
    if raw is None and request.method in ('POST', 'PUT', 'PATCH'):
        data = request.get_json(silent=True)
        if isinstance(data, dict) and 'tenant' in data:
            raw = data.get('tenant')
        elif request.form is not None and 'tenant' in request.form:
            raw = request.form.get('tenant')
    return normalize_tenant(raw, required=required)


def tenant_error_response():
    return jsonify({
        'error': '无效的业务线 tenant，可选: lvshui / yuanyi',
        'tenants': list(TENANTS.keys()),
    }), 400


def _drop_single_column_unique_index(cursor, table, column):
    """仅删除「只包含该列」的唯一索引，保留复合唯一键。"""
    cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Non_unique=0")
    groups = {}
    for row in cursor.fetchall() or []:
        key = row.get('Key_name')
        if not key or key == 'PRIMARY':
            continue
        groups.setdefault(key, []).append(row.get('Column_name'))
    for key, cols in groups.items():
        if cols == [column]:
            cursor.execute(f"ALTER TABLE `{table}` DROP INDEX `{key}`")


def _ensure_unique_index(cursor, table, index_name, columns_sql):
    cursor.execute(
        f"SHOW INDEX FROM `{table}` WHERE Key_name=%s",
        (index_name,),
    )
    if cursor.fetchone():
        return
    cursor.execute(
        f"ALTER TABLE `{table}` ADD UNIQUE KEY `{index_name}` ({columns_sql})"
    )


def _dedupe_keep_latest(cursor, table, partition_cols, order_col='id'):
    """删除分区键重复行，每个分组只保留 order_col 最大的一条。

    旧库在补 tenant 后常出现同一 CPU/指纹多行，直接加 UNIQUE 会 1062。
    """
    if not partition_cols:
        return 0
    cols_csv = ', '.join(f'`{c}`' for c in partition_cols)
    join_on = ' AND '.join(
        f'd.`{c}` <=> k.`{c}`' for c in partition_cols
    )
    cursor.execute(f"""
        DELETE d FROM `{table}` d
        INNER JOIN (
            SELECT {cols_csv}, MAX(`{order_col}`) AS keep_id
            FROM `{table}`
            GROUP BY {cols_csv}
            HAVING COUNT(*) > 1
        ) k ON {join_on}
        WHERE d.`{order_col}` < k.keep_id
    """)
    return cursor.rowcount or 0


def ensure_tenant_schema(cursor):
    """为业务表增加 tenant，并把历史数据归入铝水。"""
    tables = ('cpu_id', 'info', 'auth_log', 'device_registry')
    for table in tables:
        ensure_column(
            cursor, table, 'tenant',
            f"VARCHAR(32) NOT NULL DEFAULT '{DEFAULT_TENANT}'",
        )
        cursor.execute(
            f"UPDATE `{table}` SET tenant=%s WHERE tenant IS NULL OR tenant=''",
            (DEFAULT_TENANT,),
        )
        cursor.execute(
            f"SHOW INDEX FROM `{table}` WHERE Key_name='idx_tenant'"
        )
        if not cursor.fetchone():
            cursor.execute(f"CREATE INDEX idx_tenant ON `{table}` (tenant)")

    # 白名单 / 设备指纹唯一约束改为「租户内唯一」
    _drop_single_column_unique_index(cursor, 'cpu_id', 'CPU_Processor_ID')
    removed_cpu = _dedupe_keep_latest(
        cursor, 'cpu_id', ('tenant', 'CPU_Processor_ID'),
    )
    if removed_cpu:
        print(f'已合并重复白名单 {removed_cpu} 条（按 tenant+CPU 保留最新）')
    _ensure_unique_index(
        cursor, 'cpu_id', 'uniq_cpu_tenant', 'tenant, CPU_Processor_ID'
    )

    _drop_single_column_unique_index(cursor, 'device_registry', 'device_fingerprint')
    removed_fp = _dedupe_keep_latest(
        cursor, 'device_registry', ('tenant', 'device_fingerprint'),
    )
    if removed_fp:
        print(f'已合并重复设备指纹 {removed_fp} 条（按 tenant+指纹 保留最新）')
    _ensure_unique_index(
        cursor, 'device_registry', 'uniq_fp_tenant', 'tenant, device_fingerprint'
    )


def ensure_table_id(cursor, table):
    """旧库 info/auth_log 可能无 id 列，补齐自增主键供回填与实时动态使用。"""
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'id'")
    if cursor.fetchone():
        return
    try:
        cursor.execute(
            f"ALTER TABLE `{table}` ADD COLUMN id INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"
        )
    except Exception:
        cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN id INT NOT NULL AUTO_INCREMENT FIRST")
        cursor.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name = 'PRIMARY'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE `{table}` ADD PRIMARY KEY (id)")


def init_db():
    db_name = app.config['MYSQL_DB']
    if not db_name:
        raise RuntimeError('MYSQL_DB 未配置')

    connection = _mysql_connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
    finally:
        connection.close()

    connection = get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    Name VARCHAR(100) NOT NULL,
                    Email VARCHAR(255) NOT NULL UNIQUE,
                    Password VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cpu_id (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    CPU_Processor_ID VARCHAR(255) NOT NULL UNIQUE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS info (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    DateTime DATETIME NOT NULL,
                    IP VARCHAR(64),
                    CPU_ID VARCHAR(255),
                    Motherboard_ID VARCHAR(255),
                    Disk_ID VARCHAR(255),
                    Memory_ID VARCHAR(255),
                    INDEX idx_cpu_id (CPU_ID),
                    INDEX idx_datetime (DateTime)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    DateTime DATETIME NOT NULL,
                    CPU_ID VARCHAR(255),
                    IP VARCHAR(64),
                    result VARCHAR(16) NOT NULL,
                    reason VARCHAR(512),
                    returned_auth TINYINT(1) DEFAULT 0,
                    hardware_changed TINYINT(1) DEFAULT 0,
                    Motherboard_ID VARCHAR(255),
                    Disk_ID VARCHAR(255),
                    Memory_ID VARCHAR(255),
                    INDEX idx_cpu_id (CPU_ID),
                    INDEX idx_datetime (DateTime),
                    INDEX idx_result (result)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            for col, defn in [
                ('status', "VARCHAR(16) NOT NULL DEFAULT 'enabled'"),
                ('note', "VARCHAR(255) DEFAULT ''"),
                ('expires_at', 'DATETIME NULL'),
                ('first_auth_at', 'DATETIME NULL'),
                ('last_auth_at', 'DATETIME NULL'),
                ('auth_count', 'INT NOT NULL DEFAULT 0'),
                ('created_at', 'DATETIME DEFAULT CURRENT_TIMESTAMP'),
            ]:
                ensure_column(cursor, 'cpu_id', col, defn)

            for table in ('info', 'auth_log'):
                ensure_table_id(cursor, table)
                ensure_column(cursor, table, 'device_fingerprint', 'VARCHAR(16) NULL')
                ensure_column(cursor, table, 'is_new_device', 'TINYINT(1) NOT NULL DEFAULT 0')
                cursor.execute(
                    f"SHOW INDEX FROM `{table}` WHERE Key_name = 'idx_device_fingerprint'"
                )
                if not cursor.fetchone():
                    cursor.execute(
                        f"CREATE INDEX idx_device_fingerprint ON `{table}` (device_fingerprint)"
                    )

            backfill_device_fingerprints(cursor)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS device_registry (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    device_fingerprint VARCHAR(16) NOT NULL UNIQUE,
                    CPU_ID VARCHAR(255) NOT NULL,
                    alias VARCHAR(255) DEFAULT '',
                    status VARCHAR(16) NOT NULL DEFAULT 'enabled',
                    note VARCHAR(255) DEFAULT '',
                    Motherboard_ID VARCHAR(255),
                    Disk_ID VARCHAR(255),
                    Memory_ID VARCHAR(255),
                    last_ip VARCHAR(64),
                    first_seen_at DATETIME NULL,
                    last_seen_at DATETIME NULL,
                    auth_count INT NOT NULL DEFAULT 0,
                    INDEX idx_dr_cpu (CPU_ID),
                    INDEX idx_dr_status (status),
                    INDEX idx_dr_last_seen (last_seen_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            ensure_tenant_schema(cursor)
            backfill_device_registry(cursor)
            deleted = cleanup_old_logs(cursor)
            if deleted:
                print(f'已清理 {deleted} 条过期授权日志（保留 {LOG_RETENTION_DAYS} 天）')

        connection.commit()
        print(f'数据库 `{db_name}` 已就绪')
    finally:
        connection.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'email' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': '未登录'}), 401
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '')


def ensure_row_fingerprint(row):
    """兼容旧数据：缺失指纹时按硬件字段现场计算。"""
    if not row:
        return row
    if not row.get('device_fingerprint'):
        row['device_fingerprint'] = compute_device_fingerprint(
            row.get('CPU_ID'), row.get('Motherboard_ID'),
            row.get('Disk_ID'), row.get('Memory_ID'),
        )
    if row.get('is_new_device') is None:
        row['is_new_device'] = 0
    return row


def format_row_datetime(row, field='DateTime'):
    if row.get(field) and hasattr(row[field], 'strftime'):
        row[field] = row[field].strftime('%Y-%m-%d %H:%M:%S')
    return row


def backfill_device_fingerprints(cursor):
    """为历史记录补算设备指纹及是否新设备。"""
    for table in ('info', 'auth_log'):
        has_id = _table_has_column(cursor, table, 'id')
        if has_id:
            cursor.execute(f"""
                SELECT id, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID, device_fingerprint
                FROM `{table}`
                WHERE device_fingerprint IS NULL OR device_fingerprint = ''
            """)
            for row in cursor.fetchall():
                fp = compute_device_fingerprint(
                    row.get('CPU_ID'), row.get('Motherboard_ID'),
                    row.get('Disk_ID'), row.get('Memory_ID'),
                )
                cursor.execute(
                    f"UPDATE `{table}` SET device_fingerprint=%s WHERE id=%s",
                    (fp, row['id']),
                )
        else:
            cursor.execute(f"""
                SELECT DateTime, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID, device_fingerprint
                FROM `{table}`
                WHERE device_fingerprint IS NULL OR device_fingerprint = ''
            """)
            for row in cursor.fetchall():
                fp = compute_device_fingerprint(
                    row.get('CPU_ID'), row.get('Motherboard_ID'),
                    row.get('Disk_ID'), row.get('Memory_ID'),
                )
                cursor.execute(f"""
                    UPDATE `{table}` SET device_fingerprint=%s
                    WHERE DateTime=%s AND CPU_ID=%s
                      AND (device_fingerprint IS NULL OR device_fingerprint = '')
                    LIMIT 1
                """, (fp, row['DateTime'], row.get('CPU_ID')))

    if not _table_has_column(cursor, 'info', 'id'):
        _backfill_is_new_device_without_id(cursor, 'info')
        _backfill_is_new_device_without_id(cursor, 'auth_log', success_only=True)
        return

    cursor.execute("""
        SELECT id, device_fingerprint FROM info
        WHERE is_new_device = 0 ORDER BY DateTime ASC, id ASC
    """)
    seen = set()
    for row in cursor.fetchall():
        fp = row['device_fingerprint']
        if not fp:
            continue
        is_new = 0 if fp in seen else 1
        seen.add(fp)
        if is_new:
            cursor.execute(
                "UPDATE info SET is_new_device=1 WHERE id=%s", (row['id'],)
            )

    cursor.execute("""
        SELECT id, device_fingerprint FROM auth_log
        WHERE result='success' AND is_new_device = 0
        ORDER BY DateTime ASC, id ASC
    """)
    seen_log = set()
    for row in cursor.fetchall():
        fp = row['device_fingerprint']
        if not fp:
            continue
        if fp not in seen_log:
            seen_log.add(fp)
            cursor.execute(
                "UPDATE auth_log SET is_new_device=1 WHERE id=%s", (row['id'],)
            )
        else:
            seen_log.add(fp)


def _table_has_column(cursor, table, column):
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cursor.fetchone() is not None


def _backfill_is_new_device_without_id(cursor, table, success_only=False):
    """无 id 列时的 is_new_device 回填（按时间逐条更新）。"""
    where = "WHERE is_new_device = 0"
    if success_only:
        where += " AND result='success'"
    cursor.execute(f"""
        SELECT DateTime, CPU_ID, device_fingerprint FROM `{table}`
        {where} ORDER BY DateTime ASC
    """)
    seen = set()
    for row in cursor.fetchall():
        fp = row.get('device_fingerprint')
        if not fp:
            continue
        if fp in seen:
            continue
        seen.add(fp)
        cursor.execute(f"""
            UPDATE `{table}` SET is_new_device=1
            WHERE DateTime=%s AND CPU_ID=%s AND device_fingerprint=%s
            LIMIT 1
        """, (row['DateTime'], row.get('CPU_ID'), fp))


def cleanup_old_logs(cursor):
    """删除超过保留期的授权日志。"""
    if LOG_RETENTION_DAYS <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=LOG_RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("DELETE FROM auth_log WHERE DateTime < %s", (cutoff,))
    return cursor.rowcount


def backfill_device_registry(cursor):
    """从 info 表聚合历史设备写入 device_registry。"""
    cursor.execute("""
        SELECT tenant, device_fingerprint, CPU_ID,
               MAX(Motherboard_ID) AS Motherboard_ID,
               MAX(Disk_ID) AS Disk_ID,
               MAX(Memory_ID) AS Memory_ID,
               MIN(DateTime) AS first_seen_at,
               MAX(DateTime) AS last_seen_at,
               COUNT(*) AS auth_count,
               SUBSTRING_INDEX(GROUP_CONCAT(IP ORDER BY DateTime DESC), ',', 1) AS last_ip
        FROM info
        WHERE device_fingerprint IS NOT NULL AND device_fingerprint != ''
        GROUP BY tenant, device_fingerprint, CPU_ID
    """)
    for row in cursor.fetchall():
        fp = row['device_fingerprint']
        if not fp:
            continue
        tenant = row.get('tenant') or DEFAULT_TENANT
        cursor.execute(
            "SELECT id FROM device_registry WHERE tenant=%s AND device_fingerprint=%s",
            (tenant, fp),
        )
        if cursor.fetchone():
            cursor.execute("""
                UPDATE device_registry SET
                    CPU_ID=%s, Motherboard_ID=%s, Disk_ID=%s, Memory_ID=%s,
                    first_seen_at=LEAST(COALESCE(first_seen_at, %s), %s),
                    last_seen_at=GREATEST(COALESCE(last_seen_at, %s), %s),
                    auth_count=GREATEST(auth_count, %s),
                    last_ip=COALESCE(%s, last_ip)
                WHERE tenant=%s AND device_fingerprint=%s
            """, (
                row['CPU_ID'], row['Motherboard_ID'], row['Disk_ID'], row['Memory_ID'],
                row['first_seen_at'], row['first_seen_at'],
                row['last_seen_at'], row['last_seen_at'],
                row['auth_count'], row['last_ip'], tenant, fp,
            ))
        else:
            cursor.execute("""
                INSERT INTO device_registry
                (tenant, device_fingerprint, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
                 first_seen_at, last_seen_at, auth_count, last_ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                tenant, fp, row['CPU_ID'], row['Motherboard_ID'], row['Disk_ID'],
                row['Memory_ID'], row['first_seen_at'], row['last_seen_at'],
                row['auth_count'], row['last_ip'],
            ))


def get_device_record(cursor, fingerprint, tenant=DEFAULT_TENANT):
    cursor.execute(
        "SELECT * FROM device_registry WHERE tenant=%s AND device_fingerprint=%s",
        (tenant, fingerprint),
    )
    return cursor.fetchone()


def upsert_device_registry(cursor, hardware_info, device_fingerprint, ip, tenant=DEFAULT_TENANT):
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    cpu_id = hardware_info['cpu_id']
    existing = get_device_record(cursor, device_fingerprint, tenant)
    if existing:
        cursor.execute("""
            UPDATE device_registry SET
                last_seen_at=%s, auth_count=auth_count+1, last_ip=%s,
                Motherboard_ID=%s, Disk_ID=%s, Memory_ID=%s, CPU_ID=%s
            WHERE tenant=%s AND device_fingerprint=%s
        """, (
            now_str, ip, hardware_info['motherboard_id'],
            hardware_info['disk_id'], hardware_info['memory_id'],
            cpu_id, tenant, device_fingerprint,
        ))
    else:
        cursor.execute("""
            INSERT INTO device_registry
            (tenant, device_fingerprint, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
             first_seen_at, last_seen_at, auth_count, last_ip)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
        """, (
            tenant, device_fingerprint, cpu_id,
            hardware_info['motherboard_id'], hardware_info['disk_id'],
            hardware_info['memory_id'], now_str, now_str, ip,
        ))


def check_rate_limit(cursor, ip, cpu_id, tenant=DEFAULT_TENANT):
    if RATE_LIMIT_WINDOW_SECONDS <= 0:
        return None
    window_start = (
        datetime.now() - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
    ).strftime('%Y-%m-%d %H:%M:%S')
    if RATE_LIMIT_MAX_PER_IP > 0:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM auth_log "
            "WHERE tenant=%s AND IP=%s AND DateTime >= %s",
            (tenant, ip, window_start),
        )
        if cursor.fetchone()['cnt'] >= RATE_LIMIT_MAX_PER_IP:
            return 'IP请求过于频繁'
    if RATE_LIMIT_MAX_PER_CPU > 0:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM auth_log "
            "WHERE tenant=%s AND CPU_ID=%s AND DateTime >= %s",
            (tenant, cpu_id, window_start),
        )
        if cursor.fetchone()['cnt'] >= RATE_LIMIT_MAX_PER_CPU:
            return 'CPU授权请求过于频繁'
    return None


def check_fingerprint_policy(cursor, fingerprint, tenant=DEFAULT_TENANT):
    record = get_device_record(cursor, fingerprint, tenant)
    if record and record.get('status') == 'blocked':
        return '设备已被封禁'
    if DAILY_AUTH_LIMIT_PER_DEVICE > 0:
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM auth_log
            WHERE tenant=%s AND device_fingerprint=%s AND result='success'
              AND DATE(DateTime)=%s
        """, (tenant, fingerprint, today))
        if cursor.fetchone()['cnt'] >= DAILY_AUTH_LIMIT_PER_DEVICE:
            return '今日授权次数已达上限'
    return None


def compute_device_state(record, stale_before):
    if record.get('status') == 'blocked':
        return 'blocked'
    last_raw = record.get('last_seen_at_raw') or record.get('last_seen_at')
    if not last_raw:
        return 'never'
    if isinstance(last_raw, str):
        last_raw = datetime.strptime(last_raw, '%Y-%m-%d %H:%M:%S')
    if last_raw >= stale_before:
        return 'active'
    return 'stale'


def format_device_row(row, stale_before):
    for field in ('first_seen_at', 'last_seen_at'):
        if row.get(field) and hasattr(row[field], 'strftime'):
            row[field + '_raw'] = row[field]
            row[field] = row[field].strftime('%Y-%m-%d %H:%M:%S')
    row['state'] = compute_device_state(row, stale_before)
    row['display_name'] = row.get('alias') or row.get('device_fingerprint', '')[:8]
    return row


def fingerprint_exists(cursor, fingerprint, tenant=DEFAULT_TENANT):
    if not fingerprint:
        return False
    cursor.execute(
        "SELECT 1 FROM info WHERE tenant=%s AND device_fingerprint=%s LIMIT 1",
        (tenant, fingerprint),
    )
    return cursor.fetchone() is not None


def cpu_id_seen_before(cursor, cpu_id, tenant=DEFAULT_TENANT):
    cursor.execute(
        "SELECT 1 FROM info WHERE tenant=%s AND CPU_ID=%s LIMIT 1",
        (tenant, cpu_id),
    )
    return cursor.fetchone() is not None


def log_auth(cursor, cpu_id, ip, result, reason='', returned_auth=False,
             hardware_changed=False, hardware_info=None,
             device_fingerprint=None, is_new_device=False, tenant=DEFAULT_TENANT):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hw = hardware_info or {}
    if not device_fingerprint and hw:
        device_fingerprint = fingerprint_from_hardware(hw)
    cursor.execute("""
        INSERT INTO auth_log
        (tenant, DateTime, CPU_ID, IP, result, reason, returned_auth, hardware_changed,
         Motherboard_ID, Disk_ID, Memory_ID, device_fingerprint, is_new_device)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        tenant, now, cpu_id, ip, result, reason, int(returned_auth), int(hardware_changed),
        hw.get('motherboard_id'), hw.get('disk_id'), hw.get('memory_id'),
        device_fingerprint, int(is_new_device),
    ))
    return {
        'id': cursor.lastrowid,
        'DateTime': now,
        'CPU_ID': cpu_id,
        'IP': ip,
        'result': result,
        'reason': reason,
        'returned_auth': int(returned_auth),
        'hardware_changed': int(hardware_changed),
        'Motherboard_ID': hw.get('motherboard_id'),
        'Disk_ID': hw.get('disk_id'),
        'Memory_ID': hw.get('memory_id'),
        'device_fingerprint': device_fingerprint,
        'is_new_device': int(is_new_device),
        'tenant': tenant,
    }


def publish_auth_event(item):
    """把授权日志推给控制台 WebSocket 订阅端。"""
    if not item:
        return
    live_hub.publish({
        'type': 'auth',
        'tenant': item.get('tenant') or DEFAULT_TENANT,
        'item': item,
    })


def get_cpu_record(cursor, cpu_id, tenant=DEFAULT_TENANT):
    cursor.execute(
        "SELECT * FROM cpu_id WHERE tenant=%s AND CPU_Processor_ID=%s",
        (tenant, cpu_id),
    )
    return cursor.fetchone()


def check_cpu_whitelist(cursor, cpu_id, tenant=DEFAULT_TENANT):
    record = get_cpu_record(cursor, cpu_id, tenant)
    if not record:
        return None, 'CPU不在白名单'
    if record.get('status') == 'disabled':
        return None, '设备已禁用'
    expires = record.get('expires_at')
    if expires and expires < datetime.now():
        return None, '授权已过期'
    return record, None


def get_last_hardware(cursor, cpu_id, tenant=DEFAULT_TENANT):
    cursor.execute("""
        SELECT Motherboard_ID, Disk_ID, Memory_ID, device_fingerprint
        FROM info WHERE tenant=%s AND CPU_ID=%s
        ORDER BY DateTime DESC LIMIT 1
    """, (tenant, cpu_id))
    return cursor.fetchone()


def detect_hardware_change(cursor, cpu_id, fingerprint, tenant=DEFAULT_TENANT):
    """同 CPU 白名单下，出现与历史不同的设备指纹（另一台物理机）。"""
    if not fingerprint:
        return False
    if not cpu_id_seen_before(cursor, cpu_id, tenant):
        return False
    return not fingerprint_exists(cursor, fingerprint, tenant)


def save_success_auth(cursor, hardware_info, device_fingerprint, is_new_device,
                      tenant=DEFAULT_TENANT):
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    cpu_id = hardware_info['cpu_id']

    cursor.execute("""
        INSERT INTO info
        (tenant, DateTime, IP, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
         device_fingerprint, is_new_device)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        tenant, now_str, hardware_info['ip_address'], cpu_id,
        hardware_info['motherboard_id'], hardware_info['disk_id'],
        hardware_info['memory_id'], device_fingerprint, int(is_new_device),
    ))

    record = get_cpu_record(cursor, cpu_id, tenant)
    if record:
        if not record.get('first_auth_at'):
            cursor.execute("""
                UPDATE cpu_id SET first_auth_at=%s, last_auth_at=%s,
                auth_count=auth_count+1 WHERE tenant=%s AND CPU_Processor_ID=%s
            """, (now_str, now_str, tenant, cpu_id))
        else:
            cursor.execute("""
                UPDATE cpu_id SET last_auth_at=%s, auth_count=auth_count+1
                WHERE tenant=%s AND CPU_Processor_ID=%s
            """, (now_str, tenant, cpu_id))

    upsert_device_registry(
        cursor, hardware_info, device_fingerprint,
        hardware_info['ip_address'], tenant,
    )


# ── 页面路由 ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/message')
@login_required
def message():
    return render_template('message.html')


@app.route('/docs')
def api_docs():
    """设备授权接口说明页（公开，便于设备端对接）。"""
    auth_max = AUTH_CODE_MAX_AGE_SECONDS
    return render_template(
        'api_docs.html',
        web_port=int(os.getenv('FLASK_PORT', '7070')),
        authorize_port=int(os.getenv('AUTHORIZE_PORT', '8080')),
        auth_code_max_age_seconds=auth_max,
        auth_code_hours=(auth_max // 3600) if auth_max > 0 else 0,
        daily_limit=DAILY_AUTH_LIMIT_PER_DEVICE or '不限',
        rate_window=RATE_LIMIT_WINDOW_SECONDS,
        rate_ip=RATE_LIMIT_MAX_PER_IP,
        rate_cpu=RATE_LIMIT_MAX_PER_CPU,
    )


def _password_strong_enough(password: str) -> bool:
    return bool(re.fullmatch(
        r'(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?=.*[\W_]).{8,}',
        password or '',
    ))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = (request.form.get('Email') or '').strip()
        password = request.form.get('Password') or ''
        if not email or not password:
            flash('请输入邮箱和密码', 'login_error')
            return redirect(url_for('index'))

        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE Email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        db.close()
        if user and check_password_hash(user['Password'], password):
            session['name'] = user['Name']
            session['email'] = email
            return redirect(url_for('message'))
        flash('邮箱或密码错误，请重试', 'login_error')
        return redirect(url_for('index'))
    return redirect(url_for('index'))


@app.route('/register', methods=['POST'])
def register():
    name = (request.form.get('Name') or '').strip()
    email = (request.form.get('Email') or '').strip()
    password = request.form.get('Password') or ''

    if not name or not email or not password:
        flash('请完整填写姓名、邮箱和密码', 'register_error')
        return redirect(url_for('index'))
    if not _password_strong_enough(password):
        flash('密码须至少 8 位，且包含大小写字母、数字与特殊符号', 'register_error')
        return redirect(url_for('index'))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE Email = %s", (email,))
        if cursor.fetchone():
            flash('该邮箱已注册，请直接登录或更换邮箱', 'register_error')
            return redirect(url_for('index'))
        cursor.execute(
            "INSERT INTO users (Name, Email, Password) VALUES (%s, %s, %s)",
            (name, email, generate_password_hash(password)),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        flash(f'注册失败：{exc}', 'register_error')
        return redirect(url_for('index'))
    finally:
        cursor.close()
        db.close()

    flash('账户创建成功，请登录', 'login_success')
    return redirect(url_for('index'))


@app.route('/reset_password', methods=['POST'])
def reset_password():
    email = (request.form.get('Email') or '').strip()
    password = request.form.get('Password') or ''
    confirm = request.form.get('ConfirmPassword') or ''

    if not email or not password or not confirm:
        flash('请完整填写邮箱和新密码', 'forgot_error')
        return redirect(url_for('index'))
    if password != confirm:
        flash('两次输入的新密码不一致', 'forgot_error')
        return redirect(url_for('index'))
    if not _password_strong_enough(password):
        flash('新密码须至少 8 位，且包含大小写字母、数字与特殊符号', 'forgot_error')
        return redirect(url_for('index'))

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            "SELECT id, Password FROM users WHERE Email = %s",
            (email,),
        )
        user = cursor.fetchone()
        if not user:
            flash('该邮箱未注册，请核对后重试', 'forgot_error')
            return redirect(url_for('index'))
        if check_password_hash(user['Password'], password):
            flash('新密码不能与当前密码相同', 'forgot_error')
            return redirect(url_for('index'))
        cursor.execute(
            "UPDATE users SET Password = %s WHERE Email = %s",
            (generate_password_hash(password), email),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        flash(f'重置失败：{exc}', 'forgot_error')
        return redirect(url_for('index'))
    finally:
        cursor.close()
        db.close()

    flash('密码已重置，请使用新密码登录', 'login_success')
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.pop('name', None)
    session.pop('email', None)
    return redirect(url_for('index'))


@app.route('/api/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not old_password or not new_password or not confirm_password:
        return jsonify({'message': '请填写当前密码和新密码'}), 400
    if new_password != confirm_password:
        return jsonify({'message': '两次输入的新密码不一致'}), 400
    if old_password == new_password:
        return jsonify({'message': '新密码不能与当前密码相同'}), 400
    if not _password_strong_enough(new_password):
        return jsonify({
            'message': '新密码须至少 8 位，且包含大小写字母、数字与特殊符号',
        }), 400

    email = session.get('email')
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("SELECT Password FROM users WHERE Email = %s", (email,))
        user = cursor.fetchone()
        if not user or not check_password_hash(user['Password'], old_password):
            return jsonify({'message': '当前密码不正确'}), 400
        cursor.execute(
            "UPDATE users SET Password = %s WHERE Email = %s",
            (generate_password_hash(new_password), email),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        return jsonify({'message': f'修改失败：{exc}'}), 500
    finally:
        cursor.close()
        db.close()

    return jsonify({'message': '密码修改成功，请使用新密码重新登录'})


# ── 设备授权接口（无需登录）──────────────────────────────

@app.route('/authorize', methods=['POST'])
def authorize():
    data = request.get_json(silent=True) or {}
    cpu_id = data.get('cpu_id')
    encrypted_info = data.get('encrypted_info')
    client_ip = get_client_ip()

    # 铝水现网可不传 tenant；显式传了非法值则拒绝
    if 'tenant' in data and data.get('tenant') not in (None, ''):
        tenant = normalize_tenant(data.get('tenant'), required=True)
        if not tenant:
            return tenant_error_response()
    else:
        tenant = DEFAULT_TENANT

    if not cpu_id or not encrypted_info:
        return jsonify({'error': '缺少必要的参数'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        rate_err = check_rate_limit(cursor, client_ip, cpu_id, tenant)
        if rate_err:
            item = log_auth(cursor, cpu_id, client_ip, 'fail', rate_err, tenant=tenant)
            db.commit()
            publish_auth_event(item)
            return jsonify({'error': rate_err}), 429

        record, deny_reason = check_cpu_whitelist(cursor, cpu_id, tenant)
        if deny_reason:
            item = log_auth(cursor, cpu_id, client_ip, 'fail', deny_reason, tenant=tenant)
            db.commit()
            publish_auth_event(item)
            return jsonify({'error': deny_reason}), 400

        hardware_info, decrypt_err = decrypt_hardware_info(encrypted_info)
        if decrypt_err:
            item = log_auth(cursor, cpu_id, client_ip, 'fail', decrypt_err, tenant=tenant)
            db.commit()
            publish_auth_event(item)
            return jsonify({'error': '授权验证失败'}), 400

        if hardware_info['cpu_id'] != cpu_id:
            reason = 'CPU_ID与加密信息不匹配'
            item = log_auth(cursor, cpu_id, client_ip, 'fail', reason, tenant=tenant)
            db.commit()
            publish_auth_event(item)
            return jsonify({'error': reason}), 400

        device_fingerprint = fingerprint_from_hardware(hardware_info)
        policy_err = check_fingerprint_policy(cursor, device_fingerprint, tenant)
        if policy_err:
            item = log_auth(
                cursor, cpu_id, client_ip, 'fail', policy_err,
                hardware_info=hardware_info, device_fingerprint=device_fingerprint,
                tenant=tenant,
            )
            db.commit()
            publish_auth_event(item)
            return jsonify({'error': policy_err}), 403

        is_new_device = not fingerprint_exists(cursor, device_fingerprint, tenant)
        hw_changed = detect_hardware_change(cursor, cpu_id, device_fingerprint, tenant)
        auth_code = generate_auth_code(hardware_info)
        save_success_auth(
            cursor, hardware_info, device_fingerprint, is_new_device, tenant,
        )
        item = log_auth(
            cursor, cpu_id, client_ip, 'success', '授权成功',
            returned_auth=True, hardware_changed=hw_changed,
            hardware_info=hardware_info,
            device_fingerprint=device_fingerprint,
            is_new_device=is_new_device,
            tenant=tenant,
        )
        db.commit()
        publish_auth_event(item)
        return jsonify({
            'auth_code': auth_code,
            'hardware_changed': hw_changed,
            'device_fingerprint': device_fingerprint,
            'is_new_device': is_new_device,
            'tenant': tenant,
        })
    except Exception as exc:
        db.rollback()
        try:
            item = log_auth(cursor, cpu_id, client_ip, 'fail', str(exc), tenant=tenant)
            db.commit()
            publish_auth_event(item)
        except Exception:
            pass
        return jsonify({'error': '服务器内部错误'}), 500
    finally:
        cursor.close()
        db.close()


@app.route('/verify_auth_code', methods=['POST'])
def verify_auth_code_endpoint():
    """设备端校验授权码是否在有效期内（无需登录）。"""
    data = request.get_json(silent=True) or {}
    auth_code = data.get('auth_code', '').strip()
    if not auth_code:
        return jsonify({'valid': False, 'error': '缺少 auth_code'}), 400
    valid, reason, payload = verify_auth_code(auth_code, AUTH_CODE_MAX_AGE_SECONDS)
    if not valid:
        return jsonify({'valid': False, 'error': reason}), 400
    return jsonify({'valid': True, 'payload': payload})


# ── WebSocket 实时推送 ────────────────────────────────────

if sock:
    @sock.route('/ws/live')
    def ws_live(ws):
        """控制台订阅授权事件；同进程内 8080 授权写入后立即推到 7070 页面。"""
        if 'email' not in session:
            try:
                ws.send(live_hub.dumps({'type': 'error', 'error': '未登录'}))
            except Exception:
                pass
            return

        tenant_filter = None
        q = live_hub.subscribe()
        try:
            ws.send(live_hub.dumps({'type': 'hello', 'ok': True}))
            while True:
                try:
                    raw = ws.receive(timeout=0.15)
                except TimeoutError:
                    raw = False
                except Exception:
                    break
                if raw is None:
                    break
                if raw:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        msg = {}
                    if msg.get('type') == 'subscribe':
                        tenant_filter = normalize_tenant(msg.get('tenant'), required=False) or tenant_filter
                        ws.send(live_hub.dumps({
                            'type': 'subscribed',
                            'tenant': tenant_filter,
                        }))
                    elif msg.get('type') == 'ping':
                        ws.send(live_hub.dumps({'type': 'pong'}))

                try:
                    event = q.get(timeout=0.2)
                except Empty:
                    continue
                if tenant_filter and event.get('tenant') and event['tenant'] != tenant_filter:
                    continue
                ws.send(live_hub.dumps(event))
        finally:
            live_hub.unsubscribe(q)


# ── KPI / 统计 API ────────────────────────────────────────

@app.route('/api/tenants')
@login_required
def api_tenants():
    return jsonify({
        'default': DEFAULT_TENANT,
        'tenants': [
            {'code': t['code'], 'name': t['name'], 'tag': t['tag']}
            for t in TENANTS.values()
        ],
    })


@app.route('/api/cpu_count')
@login_required
def api_cpu_count():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM cpu_id WHERE tenant=%s AND status='enabled'",
        (tenant,),
    )
    count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'cpu_count': count, 'tenant': tenant})


@app.route('/api/authorized_count')
@login_required
def api_authorized_count():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT device_fingerprint) AS cnt FROM info
        WHERE tenant=%s AND device_fingerprint IS NOT NULL AND device_fingerprint != ''
    """, (tenant,))
    count = cursor.fetchone()['cnt']
    if not count:
        cursor.execute(
            "SELECT COUNT(DISTINCT CPU_ID) AS cnt FROM info WHERE tenant=%s",
            (tenant,),
        )
        count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'authorized_count': count, 'tenant': tenant})


@app.route('/api/today_authorized_count')
@login_required
def api_today_authorized_count():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM auth_log "
        "WHERE tenant=%s AND result='success' AND DATE(DateTime)=%s",
        (tenant, today),
    )
    count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'today_authorized_count': count, 'tenant': tenant})


@app.route('/api/today_failed_count')
@login_required
def api_today_failed_count():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM auth_log "
        "WHERE tenant=%s AND result='fail' AND DATE(DateTime)=%s",
        (tenant, today),
    )
    count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'today_failed_count': count, 'tenant': tenant})


@app.route('/api/today_new_devices_count')
@login_required
def api_today_new_devices_count():
    """今日首次出现的设备指纹数（真正的新设备）。"""
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(*) AS cnt FROM auth_log
        WHERE tenant=%s AND result='success' AND is_new_device=1 AND DATE(DateTime)=%s
    """, (tenant, today))
    count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'today_new_devices_count': count, 'tenant': tenant})


@app.route('/api/unknown_devices_count')
@login_required
def api_unknown_devices_count():
    """今日白名单外失败请求的去重 CPU 数"""
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT CPU_ID) AS cnt FROM auth_log
        WHERE tenant=%s AND result='fail' AND reason='CPU不在白名单'
          AND DATE(DateTime)=%s
    """, (tenant, today))
    count = cursor.fetchone()['cnt']
    cursor.close()
    db.close()
    return jsonify({'unknown_devices_count': count, 'tenant': tenant})


@app.route('/api/auth_trends')
@login_required
def api_auth_trends():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    days = request.args.get('days', 7, type=int)
    days = min(max(days, 1), 90)
    start = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT DATE(DateTime) AS d, result, COUNT(*) AS cnt
        FROM auth_log WHERE tenant=%s AND DATE(DateTime) >= %s
        GROUP BY DATE(DateTime), result ORDER BY d
    """, (tenant, start))
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    labels, success_data, fail_data = [], [], []
    date_map = {}
    for row in rows:
        d = row['d'].strftime('%Y-%m-%d') if hasattr(row['d'], 'strftime') else str(row['d'])
        if d not in date_map:
            date_map[d] = {'success': 0, 'fail': 0}
        date_map[d][row['result']] = row['cnt']

    for i in range(days):
        d = (datetime.now() - timedelta(days=days - 1 - i)).strftime('%Y-%m-%d')
        labels.append(d[5:])
        success_data.append(date_map.get(d, {}).get('success', 0))
        fail_data.append(date_map.get(d, {}).get('fail', 0))

    return jsonify({
        'labels': labels, 'success': success_data, 'fail': fail_data, 'tenant': tenant,
    })


@app.route('/api/auth_hourly')
@login_required
def api_auth_hourly():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    days = request.args.get('days', 7, type=int)
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT HOUR(DateTime) AS h, COUNT(*) AS cnt FROM auth_log
        WHERE tenant=%s AND result='success' AND DateTime >= %s
        GROUP BY HOUR(DateTime) ORDER BY h
    """, (tenant, start))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    hourly = [0] * 24
    for row in rows:
        hourly[row['h']] = row['cnt']
    return jsonify({
        'labels': [f'{h:02d}:00' for h in range(24)],
        'data': hourly,
        'tenant': tenant,
    })


@app.route('/api/auth_fail_reasons')
@login_required
def api_auth_fail_reasons():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    days = request.args.get('days', 7, type=int)
    days = min(max(days, 1), 90)
    start = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT reason, COUNT(*) AS cnt FROM auth_log
        WHERE tenant=%s AND result='fail' AND DATE(DateTime) >= %s
        GROUP BY reason ORDER BY cnt DESC LIMIT 12
    """, (tenant, start))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({
        'labels': [r['reason'] or '未知' for r in rows],
        'data': [r['cnt'] for r in rows],
        'tenant': tenant,
    })


@app.route('/api/policy')
@login_required
def api_policy():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    return jsonify({
        'daily_auth_limit_per_device': DAILY_AUTH_LIMIT_PER_DEVICE,
        'rate_limit_window_seconds': RATE_LIMIT_WINDOW_SECONDS,
        'rate_limit_max_per_ip': RATE_LIMIT_MAX_PER_IP,
        'rate_limit_max_per_cpu': RATE_LIMIT_MAX_PER_CPU,
        'auth_code_max_age_seconds': AUTH_CODE_MAX_AGE_SECONDS,
        'log_retention_days': LOG_RETENTION_DAYS,
        'stale_days': STALE_DAYS,
        'tenant': tenant,
        'tenant_name': TENANTS[tenant]['name'],
    })


@app.route('/api/auth_feed')
@login_required
def api_auth_feed():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    since_id = request.args.get('since_id', 0, type=int)
    db = get_db()
    cursor = db.cursor()
    if since_id:
        cursor.execute("""
            SELECT * FROM auth_log
            WHERE tenant=%s AND id > %s ORDER BY id DESC LIMIT 20
        """, (tenant, since_id))
    else:
        cursor.execute(
            "SELECT * FROM auth_log WHERE tenant=%s ORDER BY id DESC LIMIT 30",
            (tenant,),
        )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    for row in rows:
        if row.get('DateTime'):
            row['DateTime'] = row['DateTime'].strftime('%Y-%m-%d %H:%M:%S')
        ensure_row_fingerprint(row)
        if row.get('is_new_device') is None:
            row['is_new_device'] = 0
    return jsonify({
        'feed': rows,
        'max_id': rows[0]['id'] if rows else since_id,
        'tenant': tenant,
    })


# ── 授权记录 API ──────────────────────────────────────────

def _build_info_query(args, tenant):
    conditions, params = ['tenant = %s'], [tenant]
    cpu_id = args.get('cpu_id', '').strip()
    ip = args.get('ip', '').strip()
    q = args.get('q', '').strip()
    start = args.get('start', '').strip()
    end = args.get('end', '').strip()
    date = args.get('date', '').strip()

    if cpu_id:
        conditions.append('CPU_ID LIKE %s')
        params.append(f'%{cpu_id}%')
    if ip:
        conditions.append('IP LIKE %s')
        params.append(f'%{ip}%')
    if q:
        conditions.append(
            '(CPU_ID LIKE %s OR IP LIKE %s OR Motherboard_ID LIKE %s '
            'OR device_fingerprint LIKE %s)'
        )
        params.extend([f'%{q}%'] * 4)
    if date:
        conditions.append('DATE(DateTime) = DATE(%s)')
        params.append(date)
    if start:
        conditions.append('DateTime >= %s')
        params.append(f'{start} 00:00:00')
    if end:
        conditions.append('DateTime <= %s')
        params.append(f'{end} 23:59:59')

    where = ' WHERE ' + ' AND '.join(conditions)
    return where, params


@app.route('/api/info')
@login_required
def api_info():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 10, type=int)
    where, params = _build_info_query(request.args, tenant)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM info{where}", params)
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cursor.execute(
        f"""SELECT DateTime, IP, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
            device_fingerprint, is_new_device
            FROM info{where} ORDER BY DateTime DESC LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    info_data = cursor.fetchall()
    for row in info_data:
        format_row_datetime(row)
        ensure_row_fingerprint(row)
    cursor.close()
    db.close()
    return jsonify({
        'info_data': info_data,
        'current_page': page,
        'total_pages': total_pages,
        'total_count': total_count,
        'tenant': tenant,
    })


@app.route('/api/info_by_date')
@login_required
def api_info_by_date():
    return api_info()


@app.route('/api/info/export')
@login_required
def api_info_export():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    where, params = _build_info_query(request.args, tenant)
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        f"""SELECT DateTime, IP, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
            device_fingerprint, is_new_device
            FROM info{where} ORDER BY DateTime DESC""",
        params,
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['时间', 'IP', 'CPU_ID', '主板ID', '磁盘ID', '内存ID', '设备指纹', '新设备'])
    for row in rows:
        dt = row['DateTime']
        if hasattr(dt, 'strftime'):
            dt = dt.strftime('%Y-%m-%d %H:%M:%S')
        ensure_row_fingerprint(row)
        writer.writerow([dt, row['IP'], row['CPU_ID'],
                         row['Motherboard_ID'], row['Disk_ID'], row['Memory_ID'],
                         row.get('device_fingerprint') or '',
                         '是' if row.get('is_new_device') else '否'])

    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=auth_records.csv'},
    )


@app.route('/api/cpu/<cpu_id>/history')
@login_required
def api_cpu_history(cpu_id):
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 10, type=int)
    fingerprint = request.args.get('fingerprint', '').strip()
    db = get_db()
    cursor = db.cursor()
    fp_clause, fp_params = '', []
    if fingerprint:
        fp_clause = ' AND device_fingerprint = %s'
        fp_params = [fingerprint]
    cursor.execute(
        f"SELECT COUNT(*) AS cnt FROM info WHERE tenant=%s AND CPU_ID=%s{fp_clause}",
        [tenant, cpu_id] + fp_params,
    )
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cursor.execute(f"""
        SELECT DateTime, IP, CPU_ID, Motherboard_ID, Disk_ID, Memory_ID,
               device_fingerprint, is_new_device
        FROM info WHERE tenant=%s AND CPU_ID=%s{fp_clause}
        ORDER BY DateTime DESC
        LIMIT %s OFFSET %s
    """, [tenant, cpu_id] + fp_params + [page_size, offset])
    records = cursor.fetchall()
    for row in records:
        format_row_datetime(row)
        ensure_row_fingerprint(row)

    cursor.execute(
        f"SELECT MIN(DateTime) AS first_dt, MAX(DateTime) AS last_dt "
        f"FROM info WHERE tenant=%s AND CPU_ID=%s{fp_clause}",
        [tenant, cpu_id] + fp_params,
    )
    span = cursor.fetchone()
    first_auth = last_auth = None
    if span.get('first_dt'):
        first_auth = span['first_dt'].strftime('%Y-%m-%d %H:%M:%S')
    if span.get('last_dt'):
        last_auth = span['last_dt'].strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute(
        "SELECT * FROM cpu_id WHERE tenant=%s AND CPU_Processor_ID=%s",
        (tenant, cpu_id),
    )
    cpu_meta = cursor.fetchone()
    if cpu_meta:
        for field in ('first_auth_at', 'last_auth_at', 'expires_at', 'created_at'):
            if cpu_meta.get(field) and hasattr(cpu_meta[field], 'strftime'):
                cpu_meta[field] = cpu_meta[field].strftime('%Y-%m-%d %H:%M:%S')

    cursor.close()
    db.close()
    return jsonify({
        'cpu_id': cpu_id,
        'records': records,
        'total_count': total_count,
        'current_page': page,
        'total_pages': total_pages,
        'first_auth': first_auth,
        'last_auth': last_auth,
        'meta': cpu_meta,
        'tenant': tenant,
    })


# ── 授权日志 API ──────────────────────────────────────────

def _build_auth_log_query(args, tenant):
    conditions, params = ['tenant = %s'], [tenant]
    cpu_id = args.get('cpu_id', '').strip()
    ip = args.get('ip', '').strip()
    result = args.get('result', '').strip()
    start = args.get('start', '').strip()
    end = args.get('end', '').strip()

    if cpu_id:
        conditions.append('CPU_ID LIKE %s')
        params.append(f'%{cpu_id}%')
    if ip:
        conditions.append('IP LIKE %s')
        params.append(f'%{ip}%')
    if result:
        conditions.append('result = %s')
        params.append(result)
    if start:
        conditions.append('DateTime >= %s')
        params.append(f'{start} 00:00:00')
    if end:
        conditions.append('DateTime <= %s')
        params.append(f'{end} 23:59:59')

    where = ' WHERE ' + ' AND '.join(conditions)
    return where, params


@app.route('/api/auth_logs')
@login_required
def api_auth_logs():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 10, type=int)
    where, params = _build_auth_log_query(request.args, tenant)

    db = get_db()
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM auth_log{where}", params)
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cursor.execute(
        f"""SELECT id, DateTime, CPU_ID, IP, result, reason, returned_auth,
            hardware_changed, device_fingerprint, is_new_device FROM auth_log{where}
            ORDER BY DateTime DESC LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    rows = cursor.fetchall()
    for row in rows:
        format_row_datetime(row)
        ensure_row_fingerprint(row)
    cursor.close()
    db.close()
    return jsonify({
        'logs': rows,
        'current_page': page,
        'total_pages': total_pages,
        'total_count': total_count,
        'tenant': tenant,
    })


@app.route('/api/auth_logs/export')
@login_required
def api_auth_logs_export():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    where, params = _build_auth_log_query(request.args, tenant)
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        f"""SELECT DateTime, CPU_ID, IP, result, reason, returned_auth, hardware_changed,
            device_fingerprint, is_new_device
            FROM auth_log{where} ORDER BY DateTime DESC""",
        params,
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['时间', 'CPU_ID', 'IP', '结果', '原因', '返回授权码', '硬件变更', '设备指纹', '新设备'])
    for row in rows:
        dt = row['DateTime']
        if hasattr(dt, 'strftime'):
            dt = dt.strftime('%Y-%m-%d %H:%M:%S')
        ensure_row_fingerprint(row)
        writer.writerow([
            dt, row['CPU_ID'], row['IP'], row['result'], row['reason'],
            '是' if row['returned_auth'] else '否',
            '是' if row['hardware_changed'] else '否',
            row.get('device_fingerprint') or '',
            '是' if row.get('is_new_device') else '否',
        ])
    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=auth_logs.csv'},
    )


# ── CPU 白名单 API ────────────────────────────────────────

@app.route('/api/cpu_ids')
@login_required
def api_cpu_ids():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 10, type=int)
    q = request.args.get('q', '').strip()

    where, params = ' WHERE tenant=%s', [tenant]
    if q:
        where += ' AND (CPU_Processor_ID LIKE %s OR note LIKE %s)'
        params.extend([f'%{q}%', f'%{q}%'])

    db = get_db()
    cursor = db.cursor()
    cursor.execute(f"SELECT COUNT(*) AS cnt FROM cpu_id{where}", params)
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cursor.execute(
        f"""SELECT id, CPU_Processor_ID, status, note, expires_at,
            first_auth_at, last_auth_at, auth_count, created_at
            FROM cpu_id{where} ORDER BY id DESC LIMIT %s OFFSET %s""",
        params + [page_size, offset],
    )
    cpu_ids = cursor.fetchall()
    for row in cpu_ids:
        for field in ('expires_at', 'first_auth_at', 'last_auth_at', 'created_at'):
            if row.get(field) and hasattr(row[field], 'strftime'):
                row[field] = row[field].strftime('%Y-%m-%d %H:%M:%S')
    cursor.close()
    db.close()
    return jsonify({
        'cpu_ids': cpu_ids,
        'current_page': page,
        'total_pages': total_pages,
        'tenant': tenant,
    })


@app.route('/api/add_cpu_id', methods=['POST'])
@login_required
def add_cpu_id():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    data = request.get_json(silent=True) or request.form
    cpu_id = (data.get('cpu_id') or '').strip()
    note = (data.get('note') or '').strip()
    expires_at = (data.get('expires_at') or '').strip() or None

    if not cpu_id:
        return jsonify({'message': 'CPU_ID 不能为空'}), 400

    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(
            """INSERT INTO cpu_id (tenant, CPU_Processor_ID, note, expires_at)
               VALUES (%s, %s, %s, %s)""",
            (tenant, cpu_id, note, expires_at),
        )
        db.commit()
    except pymysql.IntegrityError:
        db.rollback()
        return jsonify({'message': 'CPU_ID 已存在'}), 409
    except pymysql.MySQLError as exc:
        db.rollback()
        return jsonify({'message': str(exc)}), 500
    finally:
        cursor.close()
        db.close()
    return jsonify({'message': '添加成功', 'tenant': tenant}), 201


@app.route('/api/cpu_id/<int:row_id>', methods=['PATCH'])
@login_required
def update_cpu_id(row_id):
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    data = request.get_json(silent=True) or {}
    db = get_db()
    cursor = db.cursor()
    fields, values = [], []
    for key, col in [
        ('status', 'status'), ('note', 'note'), ('expires_at', 'expires_at'),
    ]:
        if key in data:
            val = data[key]
            if key == 'expires_at' and val == '':
                val = None
            fields.append(f'{col}=%s')
            values.append(val)
    if not fields:
        return jsonify({'message': '无更新字段'}), 400
    values.extend([row_id, tenant])
    cursor.execute(
        f"UPDATE cpu_id SET {', '.join(fields)} WHERE id=%s AND tenant=%s",
        values,
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'message': '更新成功'})


@app.route('/api/cpu_id/<int:row_id>', methods=['DELETE'])
@login_required
def delete_cpu_id(row_id):
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM cpu_id WHERE id=%s AND tenant=%s", (row_id, tenant))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'message': '已删除'})


@app.route('/api/cpu_ids/import', methods=['POST'])
@login_required
def import_cpu_ids():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    file = request.files.get('file')
    if not file:
        return jsonify({'message': '请上传 CSV 文件'}), 400

    content = file.read().decode('utf-8-sig')
    reader = csv.reader(io.StringIO(content))
    added, skipped = 0, 0
    db = get_db()
    cursor = db.cursor()
    for i, row in enumerate(reader):
        if not row:
            continue
        if i == 0 and row[0].lower() in ('cpu_id', 'cpu_processor_id', 'cpu processor id'):
            continue
        cpu_id = row[0].strip()
        note = row[1].strip() if len(row) > 1 else ''
        expires = row[2].strip() if len(row) > 2 and row[2].strip() else None
        if not cpu_id:
            continue
        try:
            cursor.execute(
                "INSERT INTO cpu_id (tenant, CPU_Processor_ID, note, expires_at) "
                "VALUES (%s,%s,%s,%s)",
                (tenant, cpu_id, note, expires),
            )
            added += 1
        except pymysql.IntegrityError:
            skipped += 1
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'message': f'导入完成：新增 {added} 条，跳过 {skipped} 条重复'})


@app.route('/api/cpu_ids/export')
@login_required
def export_cpu_ids():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT CPU_Processor_ID, status, note, expires_at,
               first_auth_at, last_auth_at, auth_count
        FROM cpu_id WHERE tenant=%s ORDER BY id
    """, (tenant,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['CPU_ID', '状态', '备注', '到期时间', '首次授权', '最近授权', '授权次数'])
    for row in rows:
        def fmt(v):
            return v.strftime('%Y-%m-%d %H:%M:%S') if hasattr(v, 'strftime') else (v or '')
        writer.writerow([
            row['CPU_Processor_ID'], row['status'], row['note'],
            fmt(row['expires_at']), fmt(row['first_auth_at']),
            fmt(row['last_auth_at']), row['auth_count'],
        ])
    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=cpu_whitelist.csv'},
    )


# ── 设备指纹管理 API ──────────────────────────────────────

@app.route('/api/devices')
@login_required
def api_devices():
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    view = request.args.get('view', 'flat')
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('pageSize', 10, type=int)
    q = request.args.get('q', '').strip()
    stale_before = datetime.now() - timedelta(days=STALE_DAYS)

    db = get_db()
    cursor = db.cursor()

    if view == 'tree':
        cursor.execute("""
            SELECT dr.*, c.note AS cpu_note, c.status AS cpu_status
            FROM device_registry dr
            LEFT JOIN cpu_id c
              ON c.CPU_Processor_ID = dr.CPU_ID AND c.tenant = dr.tenant
            WHERE dr.tenant=%s
            ORDER BY dr.CPU_ID, dr.last_seen_at DESC
        """, (tenant,))
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        tree_map = {}
        counts = {'active': 0, 'stale': 0, 'blocked': 0, 'never': 0}
        for row in rows:
            format_device_row(row, stale_before)
            counts[row['state']] = counts.get(row['state'], 0) + 1
            cpu = row['CPU_ID']
            if cpu not in tree_map:
                tree_map[cpu] = {
                    'cpu_id': cpu,
                    'cpu_note': row.get('cpu_note') or '',
                    'cpu_status': row.get('cpu_status') or 'enabled',
                    'device_count': 0,
                    'devices': [],
                }
            tree_map[cpu]['devices'].append(row)
            tree_map[cpu]['device_count'] += 1

        tree = list(tree_map.values())
        if q:
            ql = q.lower()
            tree = [
                g for g in tree
                if ql in g['cpu_id'].lower()
                or ql in (g['cpu_note'] or '').lower()
                or any(
                    ql in (d.get('alias') or '').lower()
                    or ql in d['device_fingerprint'].lower()
                    for d in g['devices']
                )
            ]
        total_count = sum(g['device_count'] for g in tree)
        total_pages = max(1, (len(tree) + page_size - 1) // page_size)
        offset = (page - 1) * page_size
        page_tree = tree[offset:offset + page_size]
        return jsonify({
            'view': 'tree',
            'tree': page_tree,
            'counts': counts,
            'total_count': total_count,
            'total_groups': len(tree),
            'current_page': page,
            'total_pages': total_pages,
            'tenant': tenant,
        })

    where, params = ' WHERE dr.tenant=%s', [tenant]
    if q:
        where += """ AND (dr.device_fingerprint LIKE %s OR dr.CPU_ID LIKE %s
            OR dr.alias LIKE %s OR dr.note LIKE %s)"""
        params.extend([f'%{q}%'] * 4)

    cursor.execute(f"SELECT COUNT(*) AS cnt FROM device_registry dr{where}", params)
    total_count = cursor.fetchone()['cnt']
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    cursor.execute(f"""
        SELECT dr.*, c.note AS cpu_note, c.status AS cpu_status
        FROM device_registry dr
        LEFT JOIN cpu_id c
          ON c.CPU_Processor_ID = dr.CPU_ID AND c.tenant = dr.tenant
        {where}
        ORDER BY dr.last_seen_at DESC, dr.id DESC
        LIMIT %s OFFSET %s
    """, params + [page_size, offset])
    devices = cursor.fetchall()

    cursor.execute(
        "SELECT last_seen_at, status FROM device_registry WHERE tenant=%s",
        (tenant,),
    )
    all_rows = cursor.fetchall()
    counts = {'active': 0, 'stale': 0, 'blocked': 0, 'never': 0}
    for row in all_rows:
        st = compute_device_state(row, stale_before)
        counts[st] = counts.get(st, 0) + 1

    for row in devices:
        format_device_row(row, stale_before)

    cursor.close()
    db.close()

    return jsonify({
        'view': 'flat',
        'devices': devices,
        'counts': counts,
        'total_count': total_count,
        'current_page': page,
        'total_pages': total_pages,
        'tenant': tenant,
    })


@app.route('/api/device/<fingerprint>', methods=['PATCH'])
@login_required
def api_device_patch(fingerprint):
    tenant = resolve_request_tenant()
    if not tenant:
        return tenant_error_response()
    data = request.get_json(silent=True) or {}
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM device_registry WHERE tenant=%s AND device_fingerprint=%s",
        (tenant, fingerprint),
    )
    if not cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({'error': '设备不存在'}), 404

    updates, params = [], []
    if 'alias' in data:
        updates.append('alias=%s')
        params.append(str(data['alias']).strip())
    if 'note' in data:
        updates.append('note=%s')
        params.append(str(data['note']).strip())
    if 'status' in data and data['status'] in ('enabled', 'blocked'):
        updates.append('status=%s')
        params.append(data['status'])

    if not updates:
        cursor.close()
        db.close()
        return jsonify({'error': '无有效更新字段'}), 400

    params.extend([tenant, fingerprint])
    cursor.execute(
        f"UPDATE device_registry SET {', '.join(updates)} "
        f"WHERE tenant=%s AND device_fingerprint=%s",
        params,
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({'message': '更新成功'})


@app.route('/api/device_status')
@login_required
def api_device_status():
    """兼容旧接口，转发到 /api/devices。"""
    return api_devices()


def _looks_like_ipv4(text):
    return bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', (text or '').strip()))


def _http_get_text(url, timeout=1.2):
    try:
        from urllib.request import Request, urlopen
        req = Request(url, headers={'User-Agent': 'aluminum-verify-boot'})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore').strip()
    except Exception:
        return ''


def detect_private_ip():
    """探测内网 IP（云主机常见 10.x / 172.x）。"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(('8.8.8.8', 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
        finally:
            sock.close()
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith('127.'):
            return ip
    except OSError:
        pass
    return '127.0.0.1'


def detect_public_ip():
    """云服务器优先取公网 IP：环境变量 → 云厂商元数据 → 公网回显。"""
    for key in ('PUBLIC_HOST', 'DISPLAY_HOST', 'PUBLIC_IP'):
        value = (os.getenv(key) or '').strip()
        if value:
            return value

    # 腾讯云 / 阿里云 / AWS 等元数据（仅内网可达，超时很短）
    metadata_urls = (
        'http://metadata.tencentyun.com/latest/meta-data/public-ipv4',
        'http://metadata.tencentyun.com/meta-data/public-ipv4',
        'http://10.0.0.1/latest/meta-data/eipv4',
        'http://100.100.100.200/latest/meta-data/eipv4',
        'http://169.254.169.254/latest/meta-data/public-ipv4',
    )
    for url in metadata_urls:
        text = _http_get_text(url, timeout=0.8)
        if _looks_like_ipv4(text):
            return text

    # 外网回显（安全组需放行出站）
    for url in ('https://api.ipify.org', 'https://ifconfig.me/ip'):
        text = _http_get_text(url, timeout=1.5)
        if _looks_like_ipv4(text):
            return text
    return ''


if __name__ == '__main__':
    import threading
    from werkzeug.serving import make_server

    init_db()
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    web_port = int(os.getenv('FLASK_PORT', '7070'))
    authorize_port = int(os.getenv('AUTHORIZE_PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    if debug:
        app.debug = True

    # 0.0.0.0 只表示监听所有网卡；云主机对外展示公网 IP
    private_ip = detect_private_ip()
    public_ip = detect_public_ip() if host in ('0.0.0.0', '::', '') else ''
    display_host = public_ip or (host if host not in ('0.0.0.0', '::', '') else private_ip)

    def print_access(port, title, method='GET'):
        prefix = 'POST ' if method == 'POST' else ''
        print(f'{title}: {prefix}http://{display_host}:{port}')
        if public_ip and private_ip and public_ip != private_ip:
            print(f'  公网 http://{public_ip}:{port}  ·  内网 http://{private_ip}:{port}')
        elif host in ('0.0.0.0', '::', ''):
            print(f'  监听 {host}:{port}  ·  本机 http://127.0.0.1:{port}')
        if not public_ip and host in ('0.0.0.0', '::', ''):
            print('  未探测到公网 IP：可在 .env 设置 PUBLIC_HOST=你的弹性公网IP')

    def serve_on(port, label=''):
        try:
            server = make_server(host, port, app, threaded=True)
        except OSError as exc:
            print(f'[错误] 端口 {port} 无法监听: {exc}')
            print('  请检查：1) 端口是否被占用  2) 安全组/防火墙  3) 云主机是否禁止该端口')
            raise SystemExit(1) from exc
        title = f'服务已启动 · {label}' if label else '服务已启动'
        print_access(port, title)
        server.serve_forever()

    if authorize_port != web_port:
        threading.Thread(
            target=serve_on,
            args=(authorize_port, '设备授权'),
            daemon=True,
        ).start()
        print_access(authorize_port, '设备授权接口', method='POST')
        print('  路径 /authorize')
        serve_on(web_port, 'Web 控制台')
    else:
        serve_on(web_port)
