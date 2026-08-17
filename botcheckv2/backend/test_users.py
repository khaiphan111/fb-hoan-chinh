import asyncio
from app import db
users = db.list_users()
print("Users count:", len(users))
if len(users) > 0:
    print("Sample user:", dict(users[0]))
