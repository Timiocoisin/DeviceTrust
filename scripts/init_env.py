"""Create private configuration without overwriting an existing file."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / '.env'
if target.exists():
    raise SystemExit('.env already exists; edit it locally instead of overwriting it.')
text = (root / '.env.example').read_text(encoding='utf-8-sig')
values = {'SECRET_KEY': secrets.token_hex(32), 'AES_KEY': secrets.token_hex(16),
          'DES_KEY': secrets.token_hex(4), 'DES_IV': secrets.token_hex(4)}
lines = []
for line in text.splitlines():
    name = line.partition('=')[0]
    lines.append(f'{name}={values[name]}' if name in values else line)
with target.open('x', encoding='utf-8', newline='\n') as stream:
    stream.write('\n'.join(lines) + '\n')
try:
    target.chmod(0o600)
except OSError:
    pass
print('Created private .env with generated keys. Configure MySQL before starting.')
print('Provision the same AES/DES settings to your device client securely.')
