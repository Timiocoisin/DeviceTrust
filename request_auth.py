# -*- coding: utf-8 -*-
"""
设备端请求授权码客户端（自 Aluminum_water_detection_system/V-code/auth.py 迁入）

用法：
  python request_auth.py                  # 铝水（不传 tenant）
  python request_auth.py --tenant yuanyi  # 元一
  python request_auth.py --force          # 忽略本地 auth_code.bin，强制重新请求
"""
import argparse
import base64
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime

import requests
from Crypto.Cipher import AES, DES
from dotenv import load_dotenv

load_dotenv()

from crypto_config import AES_KEY, DES_KEY, DES_IV

AUTHORIZE_HOST = os.getenv('AUTHORIZE_HOST', '127.0.0.1')
AUTHORIZE_PORT = os.getenv('AUTHORIZE_PORT', '8080')
BIN_FILE_PATH = os.getenv('AUTH_CODE_BIN', 'auth_code.bin')
AUTH_CODE_MAX_AGE_SECONDS = int(os.getenv('AUTH_CODE_MAX_AGE_SECONDS', '86400'))


def log(message):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {message}')


def _read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            value = f.read().strip().strip('\x00')
            if value and value.lower() not in (
                'unknown', 'to be filled by o.e.m.', 'none', '0',
            ):
                return value
    except OSError:
        pass
    return None


def _run_cmd(command):
    try:
        result = subprocess.check_output(
            command, shell=True, stderr=subprocess.DEVNULL,
        ).decode(errors='ignore').strip()
        return result or None
    except (subprocess.CalledProcessError, OSError):
        return None


def _clean_hw_value(value):
    if not value:
        return None
    text = str(value).strip().strip('\x00')
    # 多行时取第一条有效值（磁盘/内存可能返回多条）
    for line in text.splitlines():
        line = line.strip().strip('"').strip("'")
        if not line:
            continue
        low = line.lower()
        if low in (
            'processorid', 'serialnumber', 'caption', 'deviceid',
            'unknown', 'to be filled by o.e.m.', 'none', 'null', '0',
        ):
            continue
        if low.startswith('error:'):
            continue
        return line
    return None


def _powershell_cim(class_name, property_name):
    """新版 Windows 已移除 wmic，改用 PowerShell CIM。"""
    ps = (
        f"(Get-CimInstance -ClassName {class_name} "
        f"| Select-Object -ExpandProperty {property_name} "
        f"| Where-Object {{ $_ -and $_.ToString().Trim() -ne '' }})"
    )
    try:
        result = subprocess.check_output(
            ['powershell', '-NoProfile', '-Command', ps],
            stderr=subprocess.DEVNULL,
        ).decode(errors='ignore').strip()
        return _clean_hw_value(result)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
        return None


def _wmic_value(command):
    """兼容仍带 wmic 的旧系统。"""
    try:
        raw = subprocess.check_output(
            command, shell=True, stderr=subprocess.DEVNULL,
        ).decode(errors='ignore')
        return _clean_hw_value(raw)
    except (subprocess.CalledProcessError, OSError):
        return None


def get_cpu_id():
    try:
        if platform.system() == 'Windows':
            result = (
                _powershell_cim('Win32_Processor', 'ProcessorId')
                or _wmic_value('wmic cpu get ProcessorId')
                or 'Unknown'
            )
        elif platform.system() == 'Linux':
            result = (
                _run_cmd("grep -m1 '^Serial' /proc/cpuinfo | awk -F: '{print $2}' | xargs")
                or _run_cmd("grep -m1 '^Hardware' /proc/cpuinfo | awk -F: '{print $2}' | xargs")
                or _run_cmd("grep -m1 '^processor' /proc/cpuinfo | awk '{print $3}'")
                or 'Unknown'
            )
        else:
            result = 'Unknown'
    except Exception as exc:
        result = f'Error: {exc}'
    return result


def get_motherboard_id():
    try:
        if platform.system() == 'Windows':
            result = (
                _powershell_cim('Win32_BaseBoard', 'SerialNumber')
                or _wmic_value('wmic baseboard get SerialNumber')
                or 'Unknown'
            )
        elif platform.system() == 'Linux':
            result = (
                _read_text('/sys/class/dmi/id/board_serial')
                or _read_text('/sys/class/dmi/id/product_serial')
                or _read_text('/sys/firmware/devicetree/base/serial-number')
                or _read_text('/etc/machine-id')
                or _run_cmd(
                    "sudo dmidecode -t baseboard 2>/dev/null | "
                    "grep 'Serial Number' | head -n1 | awk '{print $3}'"
                )
                or 'Unknown'
            )
        else:
            result = 'Unknown'
    except Exception as exc:
        result = f'Error: {exc}'
    return result


def get_disk_id():
    try:
        if platform.system() == 'Windows':
            result = (
                _powershell_cim('Win32_DiskDrive', 'SerialNumber')
                or _wmic_value('wmic diskdrive get SerialNumber')
                or 'Unknown'
            )
        elif platform.system() == 'Linux':
            result = (
                _run_cmd('lsblk -ndo SERIAL /dev/mmcblk0 2>/dev/null | head -n1')
                or _run_cmd('lsblk -ndo SERIAL /dev/nvme0n1 2>/dev/null | head -n1')
                or _run_cmd('lsblk -ndo SERIAL /dev/sda 2>/dev/null | head -n1')
                or _read_text('/sys/block/mmcblk0/device/cid')
                or _read_text('/sys/block/mmcblk0/device/serial')
                or _run_cmd('findmnt -no UUID / 2>/dev/null')
                or 'Unknown'
            )
        else:
            result = 'Unknown'
    except Exception as exc:
        result = f'Error: {exc}'
    return result


def get_memory_id():
    try:
        if platform.system() == 'Windows':
            result = (
                _powershell_cim('Win32_PhysicalMemory', 'SerialNumber')
                or _wmic_value('wmic memorychip get SerialNumber')
                or 'Unknown'
            )
        elif platform.system() == 'Linux':
            result = (
                _run_cmd(
                    "sudo dmidecode -t memory 2>/dev/null | "
                    "grep 'Serial Number' | awk '{print $3}' | head -n1"
                )
                or _run_cmd("awk '/MemTotal/ {print $2}' /proc/meminfo")
                or 'Unknown'
            )
        else:
            result = 'Unknown'
    except Exception as exc:
        result = f'Error: {exc}'
    return result


def get_hardware_info():
    return {
        'cpu_id': get_cpu_id(),
        'motherboard_id': get_motherboard_id(),
        'disk_id': get_disk_id(),
        'memory_id': get_memory_id(),
        'ip_address': socket.gethostbyname(socket.gethostname()),
    }


def encrypt_hardware_info(hardware_info):
    machine_code = '|'.join([
        hardware_info['cpu_id'],
        hardware_info['motherboard_id'],
        hardware_info['disk_id'],
        hardware_info['memory_id'],
        hardware_info['ip_address'],
    ])
    aes = AES.new(AES_KEY.encode(), AES.MODE_ECB)
    padded = machine_code.encode()
    while len(padded) % 16 != 0:
        padded += b'\0'
    return base64.b64encode(aes.encrypt(padded)).decode('utf-8')


def decrypt_auth_code(encrypted_auth_code):
    des = DES.new(DES_KEY, DES.MODE_CBC, DES_IV)
    decrypted = des.decrypt(base64.b64decode(encrypted_auth_code))
    return decrypted.rstrip(b'\0').decode('utf-8')


def send_hardware_info(cpu_id, encrypted_info, tenant=None):
    url = f'http://{AUTHORIZE_HOST}:{AUTHORIZE_PORT}/authorize'
    data = {
        'cpu_id': cpu_id,
        'encrypted_info': encrypted_info,
    }
    # 铝水现网不传 tenant；元一必须传 yuanyi
    if tenant:
        data['tenant'] = tenant

    log(f'请求授权: {url}  tenant={tenant or "lvshui(默认)"}')
    try:
        response = requests.post(url, json=data, timeout=15)
        body = response.json() if response.content else {}
        if response.status_code == 200:
            auth_code = body.get('auth_code')
            if auth_code:
                with open(BIN_FILE_PATH, 'wb') as f:
                    f.write(base64.b64decode(auth_code))
                log(f'授权成功，已写入 {BIN_FILE_PATH}')
                if body.get('device_fingerprint'):
                    log(f'设备指纹: {body["device_fingerprint"]}')
                return True
            log('响应缺少 auth_code')
        else:
            log(f'请求失败 ({response.status_code}): {body.get("error") or body}')
    except requests.exceptions.RequestException as exc:
        log(f'网络请求失败: {exc}')
    return False


def read_and_decrypt_auth_code():
    if not os.path.exists(BIN_FILE_PATH):
        return None
    with open(BIN_FILE_PATH, 'rb') as f:
        encrypted = base64.b64encode(f.read()).decode('utf-8')
    return decrypt_auth_code(encrypted)


def compare_with_expected(decrypted_auth_code, hardware_info):
    parts = decrypted_auth_code.split('|')
    if len(parts) < 5:
        log('授权码格式无效')
        return False

    timestamp = parts[-1]
    log(f'授权时间: {timestamp}')

    if AUTH_CODE_MAX_AGE_SECONDS > 0:
        try:
            issued_at = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            age = (datetime.now() - issued_at).total_seconds()
            if age > AUTH_CODE_MAX_AGE_SECONDS:
                log(f'授权码已过期（{int(age)}s > {AUTH_CODE_MAX_AGE_SECONDS}s）')
                return False
        except ValueError:
            log('授权码时间戳无法解析')
            return False

    for key, value in hardware_info.items():
        if key == 'ip_address':
            continue
        if value not in decrypted_auth_code:
            log(f'硬件信息不匹配: {key}')
            return False

    log('所有硬件信息匹配成功')
    return True


def authorize_system(tenant=None, force=False):
    hardware_info = get_hardware_info()
    log(
        '硬件信息: '
        f"cpu={hardware_info['cpu_id']}, "
        f"board={hardware_info['motherboard_id']}, "
        f"disk={hardware_info['disk_id']}, "
        f"mem={hardware_info['memory_id']}, "
        f"ip={hardware_info['ip_address']}"
    )

    if not force and os.path.exists(BIN_FILE_PATH):
        log('检测到本地授权码，尝试验证...')
        decrypted = read_and_decrypt_auth_code()
        if decrypted and compare_with_expected(decrypted, hardware_info):
            log('授权码验证通过')
            return True
        log('本地授权码无效或过期，重新请求...')

    log('正在请求新的授权码...')
    encrypted = encrypt_hardware_info(hardware_info)
    if send_hardware_info(hardware_info['cpu_id'], encrypted, tenant=tenant):
        decrypted = read_and_decrypt_auth_code()
        if decrypted and compare_with_expected(decrypted, hardware_info):
            log('授权码验证通过')
            return True
        log('拿到授权码后本地校验失败')
        return False

    log('授权码获取失败')
    return False


def main():
    parser = argparse.ArgumentParser(description='请求设备授权码')
    parser.add_argument(
        '--tenant',
        default='',
        help='业务线：留空=铝水(lvshui)；元一传 yuanyi',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='忽略本地 auth_code.bin，强制重新请求',
    )
    parser.add_argument(
        '--host',
        default='',
        help='覆盖 AUTHORIZE_HOST',
    )
    parser.add_argument(
        '--port',
        default='',
        help='覆盖 AUTHORIZE_PORT',
    )
    args = parser.parse_args()

    global AUTHORIZE_HOST, AUTHORIZE_PORT
    if args.host:
        AUTHORIZE_HOST = args.host
    if args.port:
        AUTHORIZE_PORT = args.port

    tenant = (args.tenant or '').strip() or None
    ok = authorize_system(tenant=tenant, force=args.force)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
