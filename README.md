# Bagh-e-Khas FastAPI Backend

Inventory, catalog, and order-management API backed by the existing Neon PostgreSQL database.
The current Next.js storefront remains unchanged; it can be connected to these public endpoints in
a later milestone.

## Requirements

- Python 3.11 or newer
- A Neon/PostgreSQL connection string
- A Neon branch or backup before the first production migration

## Local setup

```bash
cd Backend
cp .env.example .env
# Fill DATABASE_URL and JWT_SECRET in .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
alembic upgrade head
bagh-api seed-catalog
bagh-api create-admin
uvicorn app.main:app --reload
```

API documentation is available at `http://localhost:8000/docs`. The liveness and database
readiness endpoints are `/health/live` and `/health/ready`.

`NEON_DB_CONNECTION` is accepted as an alternative to `DATABASE_URL`, so the existing connection
string can be reused. `create-admin` reads `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD`, or
accepts `--email` and `--password`. It never changes an existing administrator's password.

## First production rollout

1. Create a Neon branch from production and point `DATABASE_URL` at that branch.
2. Run `alembic upgrade head`; verify existing customers, orders, items, and normalized statuses.
3. Run `bagh-api seed-catalog` twice and confirm the second run inserts zero rows.
4. Run `bagh-api create-admin`, start the API, and test login plus the ready endpoint.
5. Repeat against production only after the branch checks pass.

The migration is intentionally not downgradeable because converting fractional quantities back to
integers or dropping audit history could destroy data. Use a Neon branch restore for rollback.

## API behavior

- Public catalog: `GET /api/v1/catalog/products` and `GET /api/v1/catalog/products/{id}`
- Public checkout: `POST /api/v1/orders` with a UUID `Idempotency-Key` header
- Authentication: `POST /api/v1/auth/login` and `GET /api/v1/auth/me`
- Admin products/inventory: `/api/v1/admin/products`
- Admin orders: `/api/v1/admin/orders`

Order creation validates authoritative prices and availability without changing inventory.
Confirmation reserves free FIFO stock, cancellation releases active reservations, and delivery
consumes the reservation into COGS. Purchases, waste, and corrections remain explicit audited
inventory operations.

## Tests and checks

```bash
ruff check .
mypy app
pytest
```

Integration tests are skipped unless `TEST_DATABASE_URL` points to a disposable PostgreSQL
database. Never set it to the production Neon database.

## Docker

```bash
docker build -t bagh-e-khas-api .
docker run --env-file .env -p 8000:8000 bagh-e-khas-api
```

Run migrations and seed commands as one-off deployment tasks; the container does not mutate the
database automatically when it starts.
