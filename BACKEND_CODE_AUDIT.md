# Backend Code Audit

## Audit — 2026-08-14

**Scope:** `app/main.py`, configuration and session lifecycle; authentication and rider authorization; public/admin catalog and checkout; customer/address handling; order transitions and rider delivery; inventory, purchase, FIFO batch, reservation, movement, and costing services; all ORM models; Alembic revisions `0001`–`0015`; API schemas; unit and integration-test structure; and the Admin Panel's order API usage where needed to verify the active contract.

**Checks:**

- `.venv/bin/ruff check .` — passed.
- `.venv/bin/mypy app` — passed with strict mode (`42` source files).
- `.venv/bin/pytest tests/unit -q` — `56 passed`, with one third-party deprecation warning.
- `.venv/bin/pytest -q` — `56 passed, 15 skipped`; every PostgreSQL integration test was skipped by its destructive-test safety fixture.
- `.venv/bin/alembic heads` / `.venv/bin/alembic history` — one linear head, `0015_rider_mobile_auth`.
- Backend Git status — unavailable because `Backend/` and the workspace root are not Git worktrees.

**Database target:** Not connected. No migration, seed, destructive integration test, or production/data query was run.

### Executive summary

The code has strong baseline validation, typing, explicit transactions in the newer inventory services, server-authoritative pricing, row locks around high-risk stock/order mutations, and useful database checks and indexes. The most serious weakness is that the older generic order-status `PATCH` endpoint remains able to bypass the newer transactional order engine. Delivery accuracy also depends on a mutable customer address rather than an order snapshot. In addition, the public catalog advertises on-demand products that checkout currently rejects.

The clean lint/type/unit results do not invalidate the findings below. Current unit tests largely cover deterministic helpers and contracts; the PostgreSQL request, constraint, migration, and concurrency paths were not executed in this review.

### Relationship and database-structure review

| Parent / child | Implemented cardinality and delete policy | Audit result |
| --- | --- | --- |
| `customers` → `orders` | One-to-many; required phone FK; default database `NO ACTION`/restrict behavior | Referential integrity exists, but name/address are mutable profile fields and are not snapshotted on an order. |
| `customers` → `customer_addresses` | One-to-many; required FK with `RESTRICT` | No uniqueness for address identity and no database rule enforcing at most one default address. |
| `orders` → `order_items` | One-to-many; `CASCADE`; unique `(order_id, product_id)` | Appropriate historical item snapshots; deliberately no product FK. |
| `orders` → `order_fulfillment_lines` | One-to-many; `CASCADE`; one row per `order_item_id` | The database does not prove that `order_item_id` belongs to the same `order_id`; see verification risk below. |
| `orders` → `order_status_history` | One-to-many; `CASCADE`; actor uses `SET NULL` | History retention is sensible, but generic and dedicated transition paths can produce different operational effects. |
| `products` → batches, movements, reservations, purchase items, waste | One-to-many; predominantly `RESTRICT` | History is protected from product deletion. Cross-row product ownership (for example reservation product versus batch product) is application-enforced only. |
| `purchases` → items / costs | One-to-many; `RESTRICT` | Fits append-oriented procurement records; request handling does not normalize all FK/unique failures. |
| `admin_users` → refresh sessions | One-to-many; `CASCADE` | Token-session lifecycle is coherent; expired/revoked rows have no cleanup workflow. |
| `riders` ↔ `delivery_zones` | Many-to-many through unique `(rider_id, zone_id)`; `RESTRICT` | Membership is constrained, but normal order creation never assigns an order's zone. |
| `orders` → rider / delivery zone | Optional many-to-one; both `RESTRICT` | Historical references are protected, but automatic assignment is disconnected from checkout. |

### Confirmed findings

- [ ] **HIGH — Generic status updates bypass reservation, fulfillment, cancellation-release, rider, and COGS rules**
  - **Evidence:** `app/routers/admin_orders.py:123-130` exposes `PATCH /admin/orders/{order_id}`. `app/services/orders.py:365-418` applies the legacy `ORDER_TRANSITIONS` map and commits only the order/status-history changes. In contrast, `app/services/order_transitions.py:80-138`, `140-155`, `243-263`, `282-320`, and `322-352` reserve stock, maintain internal fulfillment state, require/assign a rider, consume reservations into COGS, or release reservations on cancellation. The request schema still accepts a status in `app/schemas/orders.py:191-207`.
  - **Impact:** Any authenticated API consumer can move an order through customer-visible states without the inventory effects required by the newer order engine. Examples include confirming without reservations, dispatching without a rider, delivering with no COGS, and cancelling without releasing active reservations. The current Admin Panel uses dedicated action endpoints for operational transitions, but the unsafe backend contract remains publicly exposed to every administrator token.
  - **Recommendation:** Restrict the generic patch to `admin_note` only (and a separately designed refund action if required). Route every status mutation through one authoritative transition service. Add PostgreSQL-backed HTTP tests proving that the generic endpoint cannot bypass each dedicated action.

- [ ] **HIGH — Rider deliveries use a mutable customer address instead of the order's delivery address**
  - **Evidence:** Each checkout upserts `Customer.name` and `Customer.address` in `app/services/orders.py:194-208`. `Order` stores coordinates, zone, and promise metadata but no address/name snapshot (`app/models.py:265-291`). Rider list/detail responses read `order.customer.name` and `order.customer.address` in `app/routers/rider.py:47-67`. The existing limitation is acknowledged in `docs/Backend/known-limitations.md:88-94`, but it now directly affects the authenticated Rider API.
  - **Impact:** If the same phone places a later order with a different address or name before an earlier delivery is completed, the rider can be shown the newer customer's address for the earlier order. This is a delivery-correctness and precise-location privacy failure, not only a historical-reporting limitation.
  - **Recommendation:** Add immutable `customer_name_snapshot` and `delivery_address_snapshot` columns to orders with an additive migration and backfill policy. Populate them atomically at checkout, return them in admin/rider order views, and retain the customer row only as the mutable CRM profile. Rehearse the backfill on a production-like copy because old rows cannot be reconstructed perfectly from current customer data.

- [ ] **HIGH — On-demand and preorder products are advertised as orderable but checkout rejects them at zero stock**
  - **Evidence:** `app/models.py:142-167` marks tracked `arrange_on_demand` and `preorder` products available at zero stock, and `app/routers/catalog.py:29-45` includes them in public catalog results. `app/services/orders.py:173-181` nevertheless rejects every tracked quantity greater than current stock without considering `stock_policy`. `tests/integration/test_inventory_engine.py:920-971` explicitly expects a zero-stock `arrange_on_demand` checkout to return `201`, but this test was among the PostgreSQL tests skipped in the safe suite.
  - **Impact:** A product can be returned as `is_available=true`/`available_on_demand` and then fail its advertised checkout path before the confirmation/procurement engine can record a shortage.
  - **Recommendation:** Make order creation policy-aware: enforce stock immediately only for `in_stock_only`, while allowing the explicit on-demand/preorder policies to reach confirmation and procurement. Add fast unit coverage for the policy decision plus the existing disposable-PostgreSQL HTTP test to CI.

- [ ] **MEDIUM — Automatic rider assignment is disconnected from API-created orders**
  - **Evidence:** `Order.delivery_zone_id` exists at `app/models.py:287-289`, and `RiderAssignmentService.assign_rider()` immediately returns no rider when it is null (`app/services/delivery.py:211-231`). `DeliveryZoneService.resolve_zone()` exists at `app/services/delivery.py:64-80`, but its only references are tests; `create_order()` constructs the order at `app/services/orders.py:244-261` without resolving or storing a zone. The auto-dispatch branch then returns `rider_unavailable` at `app/routers/admin_orders.py:174-190`.
  - **Impact:** Automatic dispatch cannot select a rider for ordinary public/admin API orders. Operators must manually choose a rider, and manual dispatch validates only that the rider is active, not that the rider belongs to the delivery zone.
  - **Recommendation:** Resolve and snapshot the delivery zone during order creation (with an explicit out-of-zone policy), or add an audited zone-assignment step before dispatch. Keep automatic assignment and dispatch in one transaction and validate zone eligibility for both automatic and manual assignment unless manual override is an explicit privileged operation.

- [ ] **MEDIUM — The legacy movement endpoint cannot serialize system-generated movements**
  - **Evidence:** Migration `alembic/versions/0012_inventory_service_support.py:20-23` and `InventoryMovement.actor_admin_id` at `app/models.py:400-402` make the actor nullable for reservation/sale system events. The older response schema still requires a UUID at `app/schemas/products.py:214-223`. `app/services/products.py:181-203` validates every row through that schema for `GET /admin/products/{product_id}/inventory-movements` (`app/routers/admin_products.py:101-109`). The newer detail schema correctly allows null at `app/schemas/inventory.py:116-129`.
  - **Impact:** Once a product has a system reservation or sale movement, the legacy product movement list can raise a Pydantic response-validation error and return HTTP 500.
  - **Recommendation:** Use one canonical movement response with `actor_admin_id: UUID | None`, deprecate the duplicate endpoint/schema if possible, and add an HTTP regression test containing both administrator and system movements.

- [ ] **MEDIUM — PATCH contracts accept null for required database fields and then fail at commit**
  - **Evidence:** `ProductUpdate` declares required stored fields such as `name`, `description`, `base_price_pkr`, status/type fields, and `manual_available` as nullable in `app/schemas/products.py:94-107`. `update_product()` applies every explicitly supplied value, including null, and commits in `app/services/products.py:99-123`. `CustomerUpdate.name/address` have the same mismatch in `app/schemas/customers.py:63-82`, and `app/routers/admin_customers.py:46-59` applies them directly to non-null columns. A local schema reproduction during this audit produced `{'name': None}` for both `ProductUpdate(name=None)` and `CustomerUpdate(name=None)`.
  - **Impact:** Valid JSON requests such as `{"name": null}` reach PostgreSQL, violate non-null constraints, and surface as unnormalized HTTP 500 responses instead of stable 422 errors. The failed session can also complicate any future work added after the commit attempt.
  - **Recommendation:** Distinguish clearable fields from required fields in Pydantic validators, reject explicit null for database-required columns, and add endpoint tests for every PATCH field's omitted/null/value behavior.

- [ ] **MEDIUM — Customer address defaults can become internally contradictory**
  - **Evidence:** On a new address, checkout clears all existing defaults before inserting a default (`app/services/orders.py:219-234`). When an existing non-default address is reused, it only sets that row to default and does not clear the prior default (`app/services/orders.py:235-238`). Neither `CustomerAddress.__table_args__` (`app/models.py:628-640`) nor migration `0010` enforces address uniqueness or one default per customer.
  - **Impact:** A customer can have multiple rows with `is_default=true`; `customer_detail()` merely sorts defaults first (`app/services/customers.py:147-150`), so consumers receive an ambiguous default rather than a durable invariant.
  - **Recommendation:** In one transaction, clear other defaults for both new and reused addresses. After detecting/reconciling existing duplicates, add a PostgreSQL partial unique index on `customer_phone WHERE is_default` and consider a normalized uniqueness rule for saved address identity.

- [ ] **MEDIUM — Several expected constraint conflicts escape the stable API error envelope**
  - **Evidence:** `PurchaseService.create_purchase()` inserts item product IDs without first resolving them (`app/services/inventory.py:223-283`), while `purchase_items.product_id` is a restrictive FK (`app/models.py:472-474`). Duplicate zone names and rider phones are database-unique (`app/models.py:660-675`), but their create/update services at `app/services/delivery.py:86-177` do not translate `IntegrityError`. `app/main.py:40-67` handles only `DomainError` and request validation errors; the only local `IntegrityError` translations are order idempotency and product creation.
  - **Impact:** Ordinary administrator mistakes or races (unknown product in a purchase, duplicate zone/rider identity) can produce HTTP 500 and inconsistent client error shapes. Retrying a semantically invalid request provides no actionable correction.
  - **Recommendation:** Validate referenced IDs where a domain-level 404/422 is useful, catch narrowly identified constraint names after rollback, and map them to stable conflict/validation codes. Do not convert unrelated database failures into misleading duplicate errors.

- [ ] **MEDIUM — Product creation and opening inventory are committed as separate transactions**
  - **Evidence:** `create_product()` commits the product at `app/services/products.py:76-87`, then calls `InventoryLifecycleService.record_opening_balance()` in a second transaction at lines `88-95`.
  - **Impact:** If the second transaction fails (database interruption, FK/actor race, or later validation change), the API fails after leaving a product persisted without the requested opening batch/movement. A retry then reports `product_exists`, making recovery manual.
  - **Recommendation:** Create the product, opening batch, and opening movement inside one service-owned transaction. Flush to obtain IDs, commit only after the full invariant is satisfied, and add a forced-failure rollback integration test.

- [ ] **MEDIUM — Public abuse controls are absent on login and order creation**
  - **Evidence:** `app/main.py:30-37` configures only CORS middleware. Admin login (`app/routers/auth.py:14-27`), rider login (`app/routers/rider.py:88-106`), and public order creation have no repository rate limiter, request-body limit, account/IP throttling, or bot control. This is also acknowledged in `docs/Backend/known-limitations.md:24-32`.
  - **Impact:** An internet-facing deployment is exposed to password guessing, expensive Argon2 request floods, catalog/order spam, and database growth. Pydantic field lengths do not limit request rate or total request-body size.
  - **Recommendation:** Enforce body-size and route-specific IP/account throttles at a trusted edge, with stricter controls and telemetry for both login routes and public checkout. Avoid account-only lockouts that attackers can use for denial of service.

- [ ] **LOW — Operations documentation contradicts the implemented reservation engine**
  - **Evidence:** `Backend/README.md:54-55` and `docs/Backend/known-limitations.md:53-61` state that orders never reserve/deduct/restore stock. The implemented confirmation, delivery, and cancellation paths do exactly those operations in `app/services/order_transitions.py:80-138`, `282-320`, and `322-352`. `docs/Backend/domain-rules.md` describes the newer behavior, so the repository currently contains mutually incompatible operating guidance.
  - **Impact:** Operators and future developers may apply manual stock adjustments on top of reservation/sale/release behavior, double-count inventory, or build clients against the obsolete lifecycle.
  - **Recommendation:** Update the README and known-limitations document to identify the dedicated order engine as authoritative, explain when free versus physical stock changes, and document that the legacy generic status patch must not be used for operations.

### Risks needing verification

- [ ] **MEDIUM — Database ownership pairs are not enforced across related columns**
  - **Evidence:** `order_fulfillment_lines` independently references `order_id` and `order_item_id` (`app/models.py:350-356`) but has no composite rule proving the item belongs to that order. Reservations and movements likewise carry `product_id` alongside a `batch_id` whose product membership is not database-checked (`app/models.py:389-408`, `569-580`). Current service code constructs consistent pairs, so no corrupt row was demonstrated.
  - **Verification:** On a disposable migrated database, query for mismatched fulfillment/order, reservation/batch, and movement/batch ownership. If clean, decide whether composite unique/FK constraints, triggers, or removing redundant columns best protects future imports and maintenance scripts; rehearse locks and backfill before production.

- [ ] **MEDIUM — Rider and procurement query indexes may not match active access paths**
  - **Evidence:** Rider daily load filters by `rider_id`, status, and `date(created_at)` (`app/services/delivery.py:243-255`), while `orders` has indexes on customer, creation time, and `(status, created_at)` only (`app/models.py:254-262`). Procurement reads filter fulfillment lines by status/quantity (`app/services/inventory.py:547-558`), while their index begins with `order_id` (`app/models.py:346-347`). Functional indexes also depend on the intended Karachi/UTC semantics.
  - **Verification:** Capture `EXPLAIN (ANALYZE, BUFFERS)` using production-like row counts and confirm the exact timezone/date policy. Add only measured composite/partial indexes, because extra indexes increase write and migration cost.

- [ ] **MEDIUM — Reservation/sale movement deltas may not form a reconcilable single ledger**
  - **Evidence:** Reserving stock decrements a batch and records a negative movement (`app/services/inventory.py:847-879`). Selling that already-reserved quantity does not decrement the batch again, but records another negative delta with the unchanged free-batch `resulting_quantity` (`app/services/inventory.py:932-964`). Documentation calls movements stock deltas and resulting free quantities, but no test reconciles movement sums to batch/free/physical stock.
  - **Verification:** Decide whether `delta` represents free stock, physical stock, or event quantity for each movement type. Run a receipt → reservation → sale/release reconciliation query on a disposable database, document the invariant, and add constraints/tests or separate free/physical delta fields if one column cannot express both.

- [ ] **HIGH — Migration and live-schema parity remain unverified**
  - **Evidence:** Alembic has a single linear head and model/migration definitions were statically reviewed, but migrations `0012` onward are intentionally forward-only and the suite's 15 PostgreSQL tests were skipped. Readiness checks only database connectivity, not the current Alembic revision (`app/routers/health.py`).
  - **Verification:** Against an explicitly approved disposable database and a production-like restored copy, run `alembic upgrade head`, integration tests, schema introspection/diff, constraint/index checks, legacy-data backfill validation, and representative query plans. Do not use development, shared, staging, or production data for destructive tests.

### Recommended remediation order

1. Close the generic status-transition bypass and add PostgreSQL-backed HTTP regression tests.
2. Snapshot delivery identity/address on orders and switch rider/admin delivery views to those snapshots.
3. Align on-demand checkout with stock policy and connect zone resolution to order creation/dispatch.
4. Fix movement serialization, PATCH null validation, address-default integrity, and constraint-error translation.
5. Make product/opening-inventory creation atomic and reconcile the movement-ledger semantics.
6. Update contradictory operating documentation, add edge abuse controls, then rehearse migrations/schema parity on an approved disposable target.

## Remediation review — 2026-08-14

The confirmed code findings above have been addressed in the application, schema, migration,
tests, and operating documentation. The original findings are preserved as the pre-remediation
record; this section records the current state.

### Implemented fixes

- [x] **Generic status bypass closed.** `OrderAdminUpdate` and the service reject operational
  status changes through the generic patch. Only `completed` and `refunded` bookkeeping changes
  remain there; confirmation, packing, dispatch, delivery, cancellation, and not-received use the
  dedicated transaction service. Unit coverage proves a generic `confirmed` update is rejected,
  and the PostgreSQL workflow now exercises the dedicated lifecycle.
- [x] **Order-time delivery identity added.** Orders now require customer-name and delivery-address
  snapshots. Public, administrator, summary/search, and rider responses use those snapshots rather
  than the mutable customer profile. Migration `0016_delivery_integrity` backfills old rows and
  keeps legacy inserts compatible with a `BEFORE INSERT` trigger. The unavoidable limitation is
  documented: historic values can only be backfilled from the latest customer profile.
- [x] **Stock-policy checkout aligned.** Checkout immediately enforces free stock for tracked
  `in_stock_only` products, while explicit `arrange_on_demand` and `preorder` products may create
  orders whose shortages enter procurement during confirmation.
- [x] **Zone resolution and rider eligibility connected.** Coordinate-based order creation stores a
  matching delivery zone. Automatic rider selection occurs inside dispatch's transaction, and an
  explicitly selected or reassigned rider must be active and belong to the order's zone when one
  is assigned. GeoJSON Polygon requests are validated, while malformed legacy polygons are safely
  skipped during resolution.
- [x] **Nullable system movement actors supported.** The legacy inventory movement response now
  accepts `actor_admin_id=null`, matching the ORM and migration.
- [x] **Required PATCH fields reject explicit null.** Product and customer update schemas return
  request validation errors before non-null database constraints are reached.
- [x] **One default customer address enforced.** Checkout clears previous defaults for both new and
  reused addresses. Migration `0016` deterministically reconciles existing duplicates before adding
  the partial unique index on `customer_phone WHERE is_default`.
- [x] **Expected constraint errors normalized.** Purchase product IDs are prevalidated, and named
  duplicate/FK constraints for purchases, zones, riders, and products map to stable domain errors.
  Unrecognized integrity failures are re-raised instead of being mislabeled.
- [x] **Product opening balance made atomic.** Product creation, its opening batch, and opening
  movement now share one transaction and commit only after the complete operation succeeds.
- [x] **Baseline request guards added.** A configurable body-size limit and per-client one-minute
  limits protect administrator login, rider login, and public checkout. These in-process controls
  are intentionally only a baseline; trusted-edge/distributed throttling, bot controls, and
  telemetry remain deployment work.
- [x] **Inventory lifecycle documentation corrected.** README and backend documents now consistently
  state that checkout does not change stock, confirmation reserves free stock, cancellation
  releases reservations, and delivery consumes reservations into COGS.

### Verification performed

- `.venv/bin/ruff check .` — passed.
- `.venv/bin/mypy app` — passed in strict mode (`43` source files).
- `.venv/bin/pytest -q` — `66 passed, 15 skipped`; the skipped tests are PostgreSQL integration
  tests protected by the destructive-test safety fixture. One third-party Starlette/httpx
  deprecation warning remains.
- `.venv/bin/alembic heads` / `.venv/bin/alembic history` — one linear head,
  `0016_delivery_integrity`.

No database migration or destructive PostgreSQL integration test was run because no explicitly
verified disposable database target was provided.

### Still open: database-dependent verification and design risks

- [ ] Rehearse migration `0016` and the full integration suite on an approved disposable
  PostgreSQL database, including snapshot backfill and duplicate-default reconciliation.
- [ ] Verify live-schema/Alembic parity and production-like query plans before rollout.
- [ ] Decide and test a single reconcilable meaning for reservation/sale movement deltas.
- [ ] Audit existing data for cross-row ownership mismatches before considering composite
  constraints for fulfillment lines, reservations, and movements.
- [ ] Measure rider/procurement access paths at realistic scale before adding more indexes.
- [ ] Add trusted-edge or shared-store rate limiting for multi-worker/multi-instance production.
