# -*- coding: utf-8 -*-
"""设备授权加解密逻辑（从 verification.py 迁入，统一使用 .env 配置）"""
import hashlib
import os
import base64
from datetime import datetime
from Crypto.Cipher import AES, DES
from crypto_config import AES_KEY, DES_KEY, DES_IV



def pad_to_8(value: bytes) -> bytes:
    while len(value) % 8 != 0:
        value += b'\0'
    return value


def compute_device_fingerprint(cpu_id='', motherboard_id='', disk_id='', memory_id=''):
    """CPU + 主板 + 磁盘 + 内存 组合指纹（16 位十六进制）。"""
    raw = '|'.join([
        (cpu_id or '').strip(),
        (motherboard_id or '').strip(),
        (disk_id or '').strip(),
        (memory_id or '').strip(),
    ])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def fingerprint_from_hardware(hardware_info: dict) -> str:
    return compute_device_fingerprint(
        hardware_info.get('cpu_id'),
        hardware_info.get('motherboard_id'),
        hardware_info.get('disk_id'),
        hardware_info.get('memory_id'),
    )


def decrypt_hardware_info(encrypted_text: str):
    """解密设备上报信息。返回 (hardware_info, None) 或 (None, 失败原因)。"""
    try:
        aes = AES.new(AES_KEY.encode(), AES.MODE_ECB)
        decrypted_padded = aes.decrypt(base64.b64decode(encrypted_text))
        decrypted_text = decrypted_padded.rstrip(b'\0').decode('utf-8')
        parts = decrypted_text.split('|')
        if len(parts) < 5:
            return None, '解密后字段不完整'
        return {
            'cpu_id': parts[0],
            'motherboard_id': parts[1],
            'disk_id': parts[2],
            'memory_id': parts[3],
            'ip_address': parts[-1],
        }, None
    except Exception as exc:
        return None, f'解密失败: {exc}'


def generate_auth_code(hardware_info: dict) -> str:
    current_datetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    machine_code = '|'.join([
        hardware_info['cpu_id'],
        hardware_info['motherboard_id'],
        hardware_info['disk_id'],
        hardware_info['memory_id'],
        current_datetime,
    ])
    machine_code_padded = pad_to_8(machine_code.encode())
    des = DES.new(DES_KEY, DES.MODE_CBC, DES_IV)
    encrypted = des.encrypt(machine_code_padded)
    return base64.b64encode(encrypted).decode('utf-8')


def verify_auth_code(auth_code: str, max_age_seconds: int = 86400):
    """校验授权码是否有效及是否在有效期内。返回 (valid, reason, payload)。"""
    try:
        des = DES.new(DES_KEY, DES.MODE_CBC, DES_IV)
        decrypted = des.decrypt(base64.b64decode(auth_code))
        text = decrypted.rstrip(b'\0').decode('utf-8')
        parts = text.split('|')
        if len(parts) < 5:
            return False, '授权码格式无效', None
        issued_at = datetime.strptime(parts[4], '%Y-%m-%d %H:%M:%S')
        if max_age_seconds > 0:
            age = (datetime.now() - issued_at).total_seconds()
            if age > max_age_seconds:
                return False, '授权码已过期', None
        payload = {
            'cpu_id': parts[0],
            'motherboard_id': parts[1],
            'disk_id': parts[2],
            'memory_id': parts[3],
            'issued_at': issued_at.strftime('%Y-%m-%d %H:%M:%S'),
        }
        return True, None, payload
    except Exception as exc:
        return False, f'授权码无效: {exc}', None
