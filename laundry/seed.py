"""Creates demo users for local testing (run from the laundry folder: python seed.py)."""
from werkzeug.security import generate_password_hash

from app import db, init_db

USERS = [
    # username, password, display name, admin
    ("alice", "password123", "Alice", 1),
    ("bob", "password456", "Bob", 0),
]

init_db()
with db() as con:
    for username, password, name, is_admin in USERS:
        con.execute(
            "INSERT OR IGNORE INTO users (username, password, display_name, is_admin) VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password), name, is_admin))
print("Demo users: " + ", ".join(f"{u} / {p}" for u, p, _, _ in USERS))
