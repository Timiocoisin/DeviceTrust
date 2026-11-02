"""Explicitly configured keys for the legacy device protocol."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))


def required_key(name: str, lengths: tuple[int, ...]) -> str:
    value = os.getenv(name, '')
    if len(value.encode('utf-8')) not in lengths:
        expected = ', '.join(str(length) for length in lengths)
        raise RuntimeError(f'{name} must be configured with {expected} UTF-8 bytes')
    return value


AES_KEY = required_key('AES_KEY', (16, 24, 32))
DES_KEY = required_key('DES_KEY', (8,)).encode('utf-8')
DES_IV = required_key('DES_IV', (8,)).encode('utf-8')
