"""
One-time bootstrap for the very first Super Admin account.

Why this exists: /auth/register is now gated behind get_super_admin (no
public self-signup in the multi-tenant design — see the migration to
customers/role). That's correct for every user *after* the first one, but
it creates a chicken-and-egg problem: with an empty `users` table, there
is no token that could ever be presented to /auth/register to create the
first admin. This script creates that first account directly against the
DB, bypassing the API.

Usage (from the backend/ directory, with your venv activated so
app.* imports resolve and .env is picked up):

    python scripts/seed_super_admin.py --email admin@example.com --password "Str0ngPass!" --full-name "Admin"

If a SUPER_ADMIN already exists, the script does nothing and reports who
it found — safe to re-run.
"""
import argparse
import getpass
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.session import SessionLocal  # noqa: E402
from app.models.user import User, Role  # noqa: E402
from app.core.security import hash_password  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Seed the first Super Admin user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", default="Super Admin")
    parser.add_argument("--password", help="If omitted, you'll be prompted (hidden input).")
    parser.add_argument(
        "--allow-additional",
        action="store_true",
        help=(
            "Create this Super Admin even if one already exists. Without "
            "this flag the script refuses, which is the right default for "
            "its original job (bootstrapping the very first account) but "
            "blocks adding a second admin later."
        )
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password for the new Super Admin: ")

    db = SessionLocal()

    try:
        existing_admin = (
            db.query(User)
            .filter(User.role == Role.SUPER_ADMIN)
            .first()
        )

        if existing_admin and not args.allow_additional:
            print(
                f"A Super Admin already exists: id={existing_admin.id}, "
                f"email={existing_admin.email}. Nothing to do — log in with "
                f"that account instead (or reset its password directly in "
                f"the DB if you've lost it)."
            )
            print(
                "\nTo add an ADDITIONAL Super Admin instead, re-run with "
                "--allow-additional."
            )
            return

        if existing_admin:
            # Explicitly requested. Worth stating out loud: a second Super
            # Admin has full control of the deployment, and this path
            # bypasses the API (so no audit row is written for the
            # creation — a direct DB insert has no request context).
            print(
                f"Existing Super Admin found (id={existing_admin.id}, "
                f"email={existing_admin.email}) — adding another because "
                f"--allow-additional was passed."
            )

        existing_email = db.query(User).filter(User.email == args.email).first()
        if existing_email:
            print(
                f"A user with email '{args.email}' already exists but is not "
                f"a Super Admin (role={existing_email.role}). Refusing to "
                f"overwrite — pick a different --email or promote that row "
                f"manually (UPDATE users SET role='SUPER_ADMIN' WHERE id=...)."
            )
            return

        admin = User(
            full_name=args.full_name,
            email=args.email,
            hashed_password=hash_password(password),
            role=Role.SUPER_ADMIN,
            customer_id=None
        )

        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(f"Super Admin created: id={admin.id}, email={admin.email}")
        print("Log in via POST /auth/login, then use the access_token as a")
        print("Bearer token (Swagger 'Authorize' button) to call /customers")
        print("and /auth/register.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
