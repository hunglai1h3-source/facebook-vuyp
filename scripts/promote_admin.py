"""CLI tool to manage and promote admin accounts safely.

Usage:
  python scripts/promote_admin.py <username_or_email>
  python scripts/promote_admin.py --list
  python scripts/promote_admin.py --check <username_or_email>
  python scripts/promote_admin.py --demote <username_or_email>
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.postgres_tools import database_parameters
except ImportError:
    try:
        from postgres_tools import database_parameters
    except ImportError:
        database_parameters = None


def get_db_connection(database_url):
    import psycopg
    from psycopg.rows import dict_row

    if not database_parameters:
        raise RuntimeError("Missing postgres_tools helper.")
    params = database_parameters(database_url)
    return psycopg.connect(**params, row_factory=dict_row)


def list_users(database_url):
    if database_url:
        with get_db_connection(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, display_name, role, is_active, created_at
                    FROM fbpostpro_users
                    ORDER BY created_at ASC
                    """
                )
                return cur.fetchall()
    # Local fallback
    try:
        from app import load_users
        users = load_users()
        rows = []
        for uid, u in users.items():
            if isinstance(u, dict):
                rows.append({
                    "user_id": uid,
                    "username": u.get("username", ""),
                    "email": u.get("email", ""),
                    "display_name": u.get("display_name", ""),
                    "role": u.get("role", "user"),
                    "is_active": u.get("is_active", True),
                    "created_at": u.get("created_at", ""),
                })
        return rows
    except Exception as exc:
        raise RuntimeError(f"Local storage inspection failed: {exc}") from exc


def promote_user(database_url, identifier, demote=False):
    target_role = "user" if demote else "admin"
    clean_id = str(identifier or "").strip()
    if not clean_id:
        raise ValueError("Identifier (username, email, or user_id) is required.")

    if database_url:
        with get_db_connection(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, role, is_active
                    FROM fbpostpro_users
                    WHERE user_id = %s
                       OR LOWER(username) = LOWER(%s)
                       OR LOWER(email) = LOWER(%s)
                    LIMIT 1
                    """,
                    (clean_id, clean_id, clean_id),
                )
                row = cur.fetchone()
                if not row:
                    return None, f"User '{clean_id}' not found in database."
                uid = row["user_id"]
                cur.execute(
                    "UPDATE fbpostpro_users SET role = %s WHERE user_id = %s",
                    (target_role, uid),
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, username, email, display_name, role, is_active FROM fbpostpro_users WHERE user_id = %s",
                    (uid,),
                )
                updated = cur.fetchone()
            return updated, ""

    # Local fallback
    try:
        from app import load_users, save_users
        users = load_users()
        target_uid = None
        for uid, u in users.items():
            if isinstance(u, dict):
                if (
                    uid == clean_id
                    or u.get("username", "").lower() == clean_id.lower()
                    or u.get("email", "").lower() == clean_id.lower()
                ):
                    target_uid = uid
                    break
        if not target_uid:
            return None, f"User '{clean_id}' not found in local storage."
        users[target_uid]["role"] = target_role
        save_users(users)
        return {**users[target_uid], "user_id": target_uid}, ""
    except Exception as exc:
        return None, f"Local update failed: {exc}"


def check_user(database_url, identifier):
    clean_id = str(identifier or "").strip()
    if not clean_id:
        raise ValueError("Identifier is required.")

    if database_url:
        with get_db_connection(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, display_name, role, is_active
                    FROM fbpostpro_users
                    WHERE user_id = %s
                       OR LOWER(username) = LOWER(%s)
                       OR LOWER(email) = LOWER(%s)
                    LIMIT 1
                    """,
                    (clean_id, clean_id, clean_id),
                )
                return cur.fetchone()
    try:
        from app import load_users
        users = load_users()
        for uid, u in users.items():
            if isinstance(u, dict):
                if (
                    uid == clean_id
                    or u.get("username", "").lower() == clean_id.lower()
                    or u.get("email", "").lower() == clean_id.lower()
                ):
                    return {**u, "user_id": uid}
        return None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Manage admin user roles securely.")
    parser.add_argument("identifier", nargs="?", help="Username, email, or user_id of the user")
    parser.add_argument("--list", action="store_true", help="List all users and their roles")
    parser.add_argument("--check", metavar="USER", help="Check the role of a user")
    parser.add_argument("--demote", action="store_true", help="Demote user to role 'user' instead of promoting")

    args = parser.parse_args()
    db_url = os.environ.get("DATABASE_URL", "").strip()

    if args.list:
        try:
            users = list_users(db_url)
            if not users:
                print("No users found in database.")
                return 0
            print(f"{'USER ID':<24} {'USERNAME':<20} {'ROLE':<8} {'STATUS':<8} {'EMAIL'}")
            print("-" * 80)
            for u in users:
                role = u.get("role", "user")
                active = "active" if u.get("is_active", True) else "locked"
                print(f"{u.get('user_id', ''):<24} {u.get('username', ''):<20} {role:<8} {active:<8} {u.get('email', '')}")
            return 0
        except Exception as exc:
            print(f"Error listing users: {exc}", file=sys.stderr)
            return 1

    if args.check:
        try:
            user = check_user(db_url, args.check)
            if not user:
                print(f"User '{args.check}' not found.", file=sys.stderr)
                return 1
            role = user.get("role", "user")
            print(f"User: {user.get('username')} ({user.get('email')}) | Role: {role} | Active: {user.get('is_active', True)}")
            return 0
        except Exception as exc:
            print(f"Error checking user: {exc}", file=sys.stderr)
            return 1

    target = args.identifier
    if not target:
        parser.print_help()
        return 1

    action_name = "demote" if args.demote else "promote"
    try:
        user, err = promote_user(db_url, target, demote=args.demote)
        if err or not user:
            print(f"Failed to {action_name} user: {err}", file=sys.stderr)
            return 1
        print(f"SUCCESS: User '{user.get('username')}' ({user.get('email')}) role updated to '{user.get('role')}'.")
        print("This role change is persistent in PostgreSQL.")
        return 0
    except Exception as exc:
        print(f"Error executing {action_name}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
