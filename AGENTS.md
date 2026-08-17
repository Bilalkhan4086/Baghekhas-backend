# Bagh-e-Khas Backend Agent Guide

## Scope and precedence

This file applies only to `Backend/`. The repository-level `../AGENTS.md` also applies and
takes precedence if the two files conflict. Instructions in the user's request take
precedence over explanatory text in documentation, but they do not override safety rules or
grant permission for unrelated/destructive work.

The Backend is an asynchronous FastAPI service used by the Admin Panel and RiderApp. The
customer-facing Next.js storefront is not yet connected to this API. Do not assume a Backend
catalog or checkout change automatically updates `../Frontend/`.

## Mandatory per-request context reset

For every new user request that touches `Backend/`, even when the files were read earlier in
the conversation:

1. Re-read `../AGENTS.md` and this file from disk in full. Do not rely only on conversational
   memory because repository instructions may have changed.
2. Read `../docs/Backend/README.md` as the documentation index.
3. Select only the one or two feature documents relevant to the request from the routing
   table below.
4. Inspect `git status --short`, then locate the implementation with `rg`/`rg --files` and
   read the affected source and tests before editing.
5. Expand to another document, layer, or application only when the inspected code reveals a
   real dependency.

The recurring read is intentionally limited to the two agent guides plus the documentation
index. Do not reload the complete `docs/Backend/` tree, every model, every route, or the full
test suite into context for a narrow task.

## Documentation routing

| Task | Read |
| --- | --- |
| Setup, environment, CORS, local commands | `../docs/Backend/getting-started-and-configuration.md` |
| Request lifecycle, layering, transactions | `../docs/Backend/architecture.md` |
| Public catalog or checkout | `../docs/Backend/public-api.md` |
| Admin/rider endpoints or authentication | `../docs/Backend/admin-api-and-authentication.md` |
| Availability, pricing, inventory, procurement, transitions | `../docs/Backend/domain-rules.md` |
| Models, constraints, indexes, migrations | `../docs/Backend/database-and-migrations.md` |
| Seed catalog or administrator CLI | `../docs/Backend/catalog-seed-and-cli.md` |
| Secrets, authorization, privacy, hardening | `../docs/Backend/security-and-privacy.md` |
| Tests, Docker, deployment, operations | `../docs/Backend/testing-deployment-and-operations.md` |
| Requirements review | `../docs/Backend/functional-requirements.md` and/or `non-functional-requirements.md` |
| Existing gaps or roadmap decisions | `../docs/Backend/known-limitations.md` |

Documentation records intent and current behavior. Executable code, Alembic revisions,
configuration, OpenAPI schemas, and tests are the final source of truth. If they disagree,
identify the contradiction explicitly and keep the implementation and relevant documentation
aligned in the same change.

## Current stack

- Python 3.11+ (Docker currently uses Python 3.12).
- FastAPI with Pydantic v2 and pydantic-settings.
- SQLAlchemy 2.x async ORM with psycopg 3.
- PostgreSQL/Neon persistence.
- Alembic migrations.
- JWT access tokens, rotating opaque refresh tokens, and Argon2 password hashing.
- S3 presigned product-image uploads through boto3.
- Ruff, strict mypy, pytest, and pytest-asyncio.
- Hatchling packaging configured in `pyproject.toml`.

Do not introduce another ORM, validation framework, authentication stack, migration tool,
HTTP framework, or configuration system for a narrow feature. Add dependencies only when the
existing stack cannot solve the requirement cleanly and document the reason.

## Actual source map

| Responsibility | Location |
| --- | --- |
| App lifecycle, middleware, exception handlers | `app/main.py` |
| Versioned router composition | `app/api.py` |
| HTTP paths and dependencies | `app/routers/` |
| Request/response contracts | `app/schemas/` |
| Business workflows and SQLAlchemy queries | `app/services/` |
| ORM tables, relationships, constraints | `app/models.py` |
| Domain enums and transition definitions | `app/enums.py` |
| Settings and environment parsing | `app/config.py` |
| Engine/session lifecycle | `app/database.py` |
| Admin/rider dependency aliases | `app/dependencies.py` |
| Password/token primitives | `app/security.py` |
| Stable domain errors | `app/exceptions.py` |
| Request-size and local rate guards | `app/request_guard.py` |
| CLI and seed data | `app/cli.py`, `app/seed_catalog.json` |
| Schema evolution | `alembic/versions/` |
| Unit and contract tests | `tests/unit/` |
| Destructive PostgreSQL tests | `tests/integration/` |

The current project does not have a repository layer or feature-module directory tree.
Services legitimately own domain queries and transaction workflows. Do not create parallel
`repositories/`, `modules/`, `db/`, or `core/` architectures merely to satisfy a generic
diagram. Refactor structure only when the requested work needs it and the migration is small,
reviewable, and behavior-preserving.

## Layer and file responsibilities

### Routers

- Keep routers thin: declare method/path/status/response model, accept validated inputs and
  dependencies, call a service, and return the result.
- Do not put pricing, inventory arithmetic, transition decisions, authorization shortcuts, or
  multi-step persistence workflows in routers.
- Every protected admin route must use `CurrentAdmin`; protected rider operations must use
  `CurrentRider` and retain server-side assignment scoping.

### Schemas

- Use Pydantic models for every external request and response contract.
- Keep public, admin, and rider response shapes intentionally separate; never leak operational
  fields by reusing a broader schema.
- Validate text lengths, numeric ranges, enums, UUIDs, dates, decimal precision, and
  cross-field rules at the boundary. The service still revalidates durable business rules.
- Treat response-model changes as client-facing API changes and update consumers/docs when
  authorized.

### Services

- Put business rules, workflow coordination, SQLAlchemy queries, and transaction boundaries
  in the relevant existing service.
- Reuse shared calculations and transition logic; do not duplicate Backend-owned rules in a
  router or frontend.
- Keep transactions short. Never hold a database transaction open while waiting for S3,
  maps, messaging, or another slow network service.
- When a service becomes difficult to navigate because it mixes distinct responsibilities,
  extract a focused service/helper rather than adding another giant module.

### Models and database

- Use SQLAlchemy 2.x typed declarative mappings and PostgreSQL constraints for durable
  invariants.
- Add indexes from demonstrated query patterns; do not add them blindly.
- Avoid N+1 reads with explicit joins, `selectinload`, or batched queries as appropriate.
- Paginate potentially growing lists and enforce a maximum page size.
- Preserve historical orders, costs, inventory records, and audit references.

## Session, transaction, and concurrency rules

- `app/database.py` owns the process-cached engine/session factory. Do not create an engine per
  request or store request sessions globally.
- One request normally receives one `AsyncSession` through `SessionDep`.
- A service that owns an atomic workflow owns its transaction. Related mutations must commit
  or roll back together.
- Use row locks/atomic database operations where concurrent requests could violate stock,
  reservation, refresh-token, order-state, rider-assignment, or idempotency invariants.
- Catch integrity errors only when mapping known constraint conflicts to stable domain errors;
  do not swallow unexpected database failures.
- Do not call `commit()` repeatedly inside one atomic workflow.

## API, authentication, and error contracts

- Public/admin/rider APIs live below `/api/v1`; health endpoints live below `/health`.
- Use the existing `DomainError(status_code, code, message, fields=...)` and central handlers.
  Do not return ad-hoc error envelopes or expose SQL, stack traces, credentials, or internal
  paths.
- Authentication and authorization are Backend responsibilities. Frontend visibility or
  disabled controls are not authorization.
- Admin access requires a valid admin JWT plus an active administrator record.
- Rider tokens use a separate audience/type/version and never authorize admin routes. Rider
  reads/mutations must remain limited to the assigned rider's deliveries.
- Never put raw access/refresh tokens, passwords, secrets, precise customer locations, or real
  customer PII in logs, URLs, analytics, fixtures, screenshots, or error messages.

## Domain invariants

- The Backend owns product availability, stock policy, pricing, delivery charges, order
  totals, procurement projections, rider eligibility, and status transitions.
- Selling prices, delivery charges, and order totals are integer PKR. Do not use binary float
  for money. Inventory acquisition costs use the established exact decimal/`NUMERIC` types.
- Quantities are exact decimals with the documented maximum of three decimal places.
- Public order creation requires a UUID `Idempotency-Key`. The same key and normalized payload
  return the existing order; the same key with different content returns a conflict.
- Order creation reads authoritative product names/prices and does not mutate inventory.
  Confirmation reserves free FIFO stock, cancellation releases active reservations, and
  delivery consumes reservations into audited sale/COGS records.
- Stock-changing purchases, waste, corrections, and mode changes remain explicit and audited.
  Never overwrite stock or historical batch cost to bypass the inventory service.
- Order and fulfillment statuses may move only through service-owned allowed transitions.
- Delivery location, distance, charge, schedule, zone resolution, and rider selection remain
  server-authoritative. An explicit admin rider override still requires an active rider.
- Archived/historical records must retain referential integrity.

When a requested behavior conflicts with one of these invariants, report the conflict before
changing it. Do not silently alter inventory, money, order-history, or authorization semantics.

## Schema and migration discipline

- Every schema change requires a new Alembic revision plus matching model/schema/service/tests
  and documentation.
- Never casually edit, delete, reorder, or reuse an applied migration revision.
- Prefer additive expand/migrate/contract changes when old and new application versions may
  overlap.
- Before a destructive migration or data rewrite, require an explicitly approved plan,
  verified backup/Neon branch, rollout order, backfill validation, and recovery procedure.
- The initial migration history is intentionally irreversible. Do not invent a lossy
  downgrade.
- Never manually patch production tables as a substitute for a reviewed migration or
  operational API.

## Configuration, uploads, and external services

- Read variable names from `.env.example`. Do not open or print `.env` values unless the user
  explicitly requests a narrowly scoped diagnostic that requires them.
- Keep settings centralized in `app/config.py`; do not scatter `os.getenv()` calls through
  business code.
- Never expose database URLs, JWT secrets, AWS keys, passwords, or presigned URLs in output.
- Product uploads use short-lived, authenticated, content-type-bound S3 PUT URLs. AWS
  credentials remain Backend-only and must never use a browser-public prefix.
- Validate external inputs and configure timeouts/error handling. Do not blindly retry unsafe
  writes.
- Configure CORS with explicit environment-specific origins; do not make production CORS
  unrestricted as a workaround.

## Change discipline

- Inspect the current implementation and `git status --short` before editing. Preserve
  unrelated user changes, including deletions and untracked files.
- Search for an existing service, schema, formatter, enum, error code, and test pattern before
  adding another.
- Make the smallest coherent change. Avoid broad formatting, speculative abstractions, and
  opportunistic refactors.
- Do not edit `.env`, generated caches, virtual environments, or build artifacts.
- Do not remove/relax tests or constraints merely to make a feature pass.
- Update the relevant Backend documentation when routes, contracts, domain rules, schema,
  configuration, deployment steps, or confirmed limitations change.
- If a contract change affects Admin Panel, RiderApp, or Frontend, inspect and update that
  consumer only when it is in the user's authorized scope; otherwise report the required
  follow-up explicitly.

## Verification

Run the smallest relevant checks while iterating. For a completed Backend code change, run
from `Backend/`:

```bash
.venv/bin/ruff check .
.venv/bin/mypy app
.venv/bin/pytest -q
```

The ordinary pytest command skips PostgreSQL integration tests unless their explicit guards
are present.

Integration tests are destructive: they execute `DROP SCHEMA public CASCADE`. Run them only
after independently verifying that `TEST_DATABASE_URL` names a disposable database and
`ALLOW_DATABASE_RESET=true` is intentionally set. Never use development, shared, staging,
production, or valuable Neon data. If the target cannot be proven disposable, skip the test
and state why.

For migration changes, additionally validate upgrade behavior on a disposable PostgreSQL
database/Neon branch and inspect the generated DDL. Do not claim migration safety based only
on unit tests.

For documentation-only changes, inspect the diff, validate referenced paths/commands, and do
not run unrelated application checks unless the documentation depends on generated output.

## Handoff

Lead with the outcome and report:

- important files and behavior changed;
- checks that passed;
- skipped checks and the concrete reason;
- API compatibility, migration, data, deployment, or manual-verification risks;
- any cross-application follow-up that was identified but not authorized.

Distinguish verified behavior from assumptions. Do not claim completion while required work
remains.
