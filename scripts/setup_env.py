"""Create local configuration without storing plaintext passwords."""
import getpass
import hashlib
import json
from pathlib import Path
import secrets


def credentials(role, default):
    username = input(f"{role} username [{default}]: ").strip() or default
    while True:
        password = getpass.getpass(f"{role} password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password and password == confirmation:
            salt = secrets.token_bytes(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
            return username, salt.hex() + ":" + digest.hex()
        print("Passwords must be nonempty and match. Please try again.")


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    if target.exists():
        raise SystemExit(".env already exists; it has not been changed.")
    admin, admin_hash = credentials("Admin", "admin")
    member, member_hash = credentials("Member", "member")
    if admin == member:
        raise SystemExit("Admin and member usernames must differ. Run again.")
    with target.open("x", encoding="utf-8") as output:
        output.write(
            f"ADMIN_USER={json.dumps(admin, ensure_ascii=False)}\nADMIN_HASH={admin_hash}\n"
            f"MEMBER_USER={json.dumps(member, ensure_ascii=False)}\nMEMBER_HASH={member_hash}\n"
            "PHOTO_PORT=8081\nCOOKIE_SECURE=0\n"
        )
    target.chmod(0o600)
    print("Created .env. Keep this file private; do not commit it.")


if __name__ == "__main__":
    main()
