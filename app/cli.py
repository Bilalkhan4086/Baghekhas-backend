import argparse
import asyncio
import json
import os
import sys
import uuid
from importlib.resources import files

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import dispose_engine, get_session_factory
from app.models import AdminUser, Product
from app.security import hash_password


async def create_admin(email: str, password: str) -> int:
    try:
        normalized_email = validate_email(email, check_deliverability=False).normalized.lower()
        encoded_password = hash_password(password)
    except (EmailNotValidError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    async with get_session_factory()() as session:
        existing = await session.scalar(
            select(AdminUser).where(func.lower(AdminUser.email) == normalized_email)
        )
        if existing is not None:
            print(f"Administrator already exists: {normalized_email}")
            return 0
        session.add(
            AdminUser(
                id=uuid.uuid4(),
                email=normalized_email,
                password_hash=encoded_password,
                is_active=True,
            )
        )
        await session.commit()
    print(f"Created administrator: {normalized_email}")
    return 0


async def seed_catalog() -> int:
    catalog_path = files("app").joinpath("seed_catalog.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    async with get_session_factory()() as session:
        before = await session.scalar(select(func.count()).select_from(Product)) or 0
        await session.execute(
            pg_insert(Product).values(catalog).on_conflict_do_nothing(index_elements=[Product.id])
        )
        await session.commit()
        after = await session.scalar(select(func.count()).select_from(Product)) or 0
    inserted = after - before
    print(f"Catalog seed complete: {inserted} inserted, {len(catalog) - inserted} skipped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bagh-e-Khas backend management commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    admin_parser = subparsers.add_parser("create-admin", help="Create the first administrator")
    admin_parser.add_argument("--email", default=os.getenv("INITIAL_ADMIN_EMAIL"))
    admin_parser.add_argument("--password", default=os.getenv("INITIAL_ADMIN_PASSWORD"))

    subparsers.add_parser("seed-catalog", help="Insert missing products from the frontend catalog")
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        if args.command == "create-admin":
            if not args.email or not args.password:
                print(
                    "Error: provide --email/--password or the INITIAL_ADMIN_EMAIL and "
                    "INITIAL_ADMIN_PASSWORD environment variables",
                    file=sys.stderr,
                )
                return 2
            return await create_admin(args.email, args.password)
        if args.command == "seed-catalog":
            return await seed_catalog()
        return 2
    finally:
        await dispose_engine()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
