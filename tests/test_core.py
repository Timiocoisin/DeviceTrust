"""Offline protocol and view checks; no database or device requests."""
import base64
import os
import secrets
import subprocess
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.update({'SECRET_KEY': secrets.token_hex(32), 'AES_KEY': secrets.token_hex(16),
                   'DES_KEY': secrets.token_hex(4), 'DES_IV': secrets.token_hex(4)})
from Crypto.Cipher import AES, DES
from auth_service import (AES_KEY, DES_KEY, DES_IV, compute_device_fingerprint,
                          decrypt_hardware_info, generate_auth_code, pad_to_8, verify_auth_code)
from app import app
from live_hub import LiveHub


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.hardware = dict(cpu_id='TEST-CPU', motherboard_id='TEST-BOARD',
                             disk_id='TEST-DISK', memory_id='TEST-RAM', ip_address='127.0.0.1')

    def test_hardware_payload_round_trip(self):
        raw = '|'.join(self.hardware.values()).encode()
        raw += b'\0' * (-len(raw) % 16)
        encrypted = base64.b64encode(AES.new(AES_KEY.encode(), AES.MODE_ECB).encrypt(raw)).decode()
        hardware, error = decrypt_hardware_info(encrypted)
        self.assertIsNone(error)
        self.assertEqual(hardware, self.hardware)

    def test_authorization_code_round_trip(self):
        valid, reason, payload = verify_auth_code(generate_auth_code(self.hardware))
        self.assertTrue(valid)
        self.assertIsNone(reason)
        self.assertEqual(payload['cpu_id'], 'TEST-CPU')

    def test_expired_code_is_rejected(self):
        timestamp = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        raw = '|'.join(['TEST-CPU', 'TEST-BOARD', 'TEST-DISK', 'TEST-RAM', timestamp])
        code = base64.b64encode(DES.new(DES_KEY, DES.MODE_CBC, DES_IV).encrypt(pad_to_8(raw.encode()))).decode()
        self.assertFalse(verify_auth_code(code, 86400)[0])
        self.assertTrue(verify_auth_code(code, 0)[0])

    def test_fingerprint_changes_with_hardware(self):
        first = compute_device_fingerprint('cpu', 'board', 'disk', 'ram')
        self.assertEqual(len(first), 16)
        self.assertEqual(first, compute_device_fingerprint('cpu', 'board', 'disk', 'ram'))
        self.assertNotEqual(first, compute_device_fingerprint('cpu', 'board', 'other', 'ram'))

    def test_malformed_code_is_rejected(self):
        self.assertFalse(verify_auth_code('not-an-authorization-code')[0])

    def test_invalid_key_fails(self):
        result = subprocess.run([sys.executable, '-c', 'import crypto_config'],
                                cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, AES_KEY='invalid'), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AES_KEY must be configured', result.stderr)


class ViewTests(unittest.TestCase):
    def test_public_views_and_protected_api(self):
        with app.test_client() as client:
            self.assertEqual(client.get('/').status_code, 200)
            docs = client.get('/docs')
            self.assertEqual(docs.status_code, 200)
            self.assertIn(b'flow-native', docs.data)
            self.assertEqual(client.get('/message').status_code, 302)
            self.assertEqual(client.get('/api/info').status_code, 401)
            self.assertEqual(client.post('/verify_auth_code', json={}).status_code, 400)

    def test_console_session_and_logout(self):
        with app.test_client() as client:
            with client.session_transaction() as session:
                session['email'] = 'test@example.com'
                session['name'] = 'Test'
            self.assertEqual(client.get('/message').status_code, 200)
            client.get('/logout')
            self.assertEqual(client.get('/api/info').status_code, 401)


class HubTests(unittest.TestCase):
    def test_subscriber_receives_event(self):
        hub = LiveHub()
        queue = hub.subscribe()
        hub.publish({'tenant': 'yuanyi', 'result': 'success'})
        self.assertEqual(queue.get_nowait()['tenant'], 'yuanyi')
        hub.unsubscribe(queue)


class BootstrapTests(unittest.TestCase):
    def test_private_configuration_is_generated_without_overwrite(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'scripts').mkdir()
            shutil.copy2(root / '.env.example', target / '.env.example')
            shutil.copy2(root / 'scripts/init_env.py', target / 'scripts/init_env.py')
            command = [sys.executable, str(target / 'scripts/init_env.py')]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            contents = (target / '.env').read_bytes()
            from dotenv import dotenv_values
            values = dotenv_values(target / '.env')
            self.assertEqual(len(values['AES_KEY'].encode()), 32)
            self.assertEqual(len(values['DES_KEY'].encode()), 8)
            self.assertNotIn(values['AES_KEY'], first.stdout)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual((target / '.env').read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
