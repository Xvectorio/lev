"""Create or reset an admin account: docker compose exec -it api python reset_admin.py"""
import getpass

from app import hash_password
from core import db, initialize

username = input('Username [admin]: ').strip() or 'admin'
password = getpass.getpass('New password (at least 12 characters): ')
if len(password) < 12:
    raise SystemExit('Password must be at least 12 characters')
initialize()
with db() as conn:
    conn.execute('''INSERT INTO users(username,password_hash) VALUES (%s,%s)
        ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash''', (username, hash_password(password)))
    conn.execute('DELETE FROM sessions WHERE username=%s', (username,))
print(f'Password set for {username}; their existing sessions were logged out.')
