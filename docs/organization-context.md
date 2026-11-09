# Organization context and tenant boundaries

Apply `alembic upgrade head` before starting the application. The migrations retain
existing organizations, memberships and shops, backfill users as active, add
composite ownership constraints and introduce private attachments, append-only
audit events and financial retry records. No financial balances are synthesized.

Phases 1–5 provide the access and posting boundaries. Financial workflows, contacts,
cashboxes, currency configuration and their business rules remain in Phases 6–12.
Their implementations must use these boundaries; completion of Phase 5 does not
mean those later workflows or endpoints exist.

## Authentication, selection and roles

Select an organization with
`POST /api/v1/auth/organizations/{organization_uuid}/select`.
`GET /api/v1/auth/context` returns its verified organization ID, acting membership,
user ID and effective permissions. Request headers/query parameters cannot override
selection. Inactive users and invalid tokens receive 401, missing selection or an
inactive organization receives 403, and missing/inactive membership receives 404.
Organization-management URL routes retain their explicit scope and allow the owner
to manage an inactive organization; selected EMS context always rejects it.

New organizations receive protected owner, cashier and accountant roles. Startup
provisions missing system defaults for existing organizations and registers new
system permission definitions without overwriting tenant-created roles. Grants
are assigned to staff explicitly through the member-role API. The active owner
with active membership retains all permissions. Cashier/accountant defaults exclude
reversals, refunds, closing approval, settings changes and staff administration.
Private attachments require `attachment:read`/`attachment:manage`.

`CurrentOrganization`, `CurrentMembership`, `CurrentUserPermissions`, and
`CurrentOrganizationContext` are reusable dependencies. Every action must check its
fixed permission name, never one supplied by the client. Never authorize using
`User.permissions`, which aggregates across organizations. The permission cache
includes organization, RBAC version and user identity; mutations invalidate the
version, and membership is always revalidated before using grants.

## Ownership and ORM enforcement

`CurrentOrganizationContext` opens an ORM organization scope through the endpoint's
final flush. The same guard applies inside posting and job callbacks. SQLAlchemy
adds organization predicates to reads, joins, aggregates, ORM updates and deletes,
including aliased entities. New rows acquire the verified organization and foreign
organization writes are rejected. Bulk ownership reassignment, textual/Core SQL
and bulk INSERT are rejected inside this scope. Use a reviewed migration for
administrative SQL outside an EMS request.

Services can use `context.query(Model)`, `get`, `new`, `shop`, and `reference` with
an explicit context even outside a request. UUIDs are used in public resource paths;
service-level integer IDs are always qualified by organization. `reference` validates
a record's organization, active state and, when supplied, expected shop before
posting. Queries containing multiple joined owned models should also pass them to
`context.query(Model, OtherModel)` or execute inside the guarded ORM scope.

Every new business model must inherit `OrganizationOwned` or `ShopOwned`. These add
non-null indexed organization ownership and restrictive organization/shop foreign
keys. `ShopOwned` includes composite organization/shop uniqueness for same-shop
references. Add `owned_reference(...)` to organization-only references or
`shop_reference(...)` to same-shop links: contacts, cashboxes, currencies, posted
transactions and attachments cannot be linked across their required boundary.
When defining custom table arguments, retain the mixin's uniqueness constraints.
Use `ImmutableHistory` for append-only posted tables. It installs database triggers
on PostgreSQL, SQLite and MySQL that reject UPDATE and DELETE, including bulk SQL.

## Transactional posting and retries

`TenantOperations.execute_once` requires a nonempty retry key, operation, complete
payload, fixed action permission and an async posting callback. Pass every referenced
record as `(Model, record_id, expected_shop_id_or_None)`. Ownership is checked before
the callback runs. It serializes postings through the organization's database row
lock (a write lock on SQLite), validates current access, and stores the result with
the posting in one transaction. The caller commits the transaction only after the
entire operation succeeds. The callback may use savepoints but cannot commit the
posting transaction itself. Failed callbacks leave no partial writes or retry row.

Retry uniqueness is `(organization_id, operation, retry_key)`. Identical payloads
return the original JSON result; changed payloads receive 409; another acting
membership cannot retrieve the result. Access is rechecked even on a retry hit.
References and financial values must be included in the hashed payload. `Money`
transports an exact decimal string and explicit uppercase currency code, rejects
binary floats/scientific notation/non-finite values, and checks configured precision
without silently rounding. Rates, cost accounting, permitted negative balances and
currency rounding policies are defined with their financial modules in Phase 6.

Posting adds an immutable audit event with organization, acting membership, user,
action, retry reference and timestamp. `TenantOperations.audit` records other events
in the caller's transaction. Audit data has no free-form request payload or file
content. SQL engines hide bound parameters, and validation responses omit input
values. Validation uses HTTP 422 and a consistent `type`/`message` envelope with
field details where available; missing records return 404 and conflicts return 409.

## Jobs, caches, private files and history

Queue `context.job_payload()` and execute through `TenantOperations.run_job` with
fixed required permissions. Execution revalidates organization, user, membership ID
and membership UUID. Deleting/recreating a membership cannot revive an old job even
if an integer ID is reused. Job writes are scoped and transactional. Financial jobs
must also use `execute_once` with a stable operation/retry key.

`cache_get`/`cache_set` scope responses by organization and acting membership UUID
and recheck permissions on every access. Cache identifiers are encoded without
ambiguous string concatenation. Redis failure falls back to a cache miss.

`POST /api/v1/files` accepts `shop_uuid` and a multipart `file`, validates active
shop ownership, file name, 1-byte–5-MiB size and PDF/PNG/JPEG content signature.
Files are stored privately in the database. `GET /api/v1/files/{attachment_uuid}`
revalidates access and returns a download with `no-store` and `nosniff` headers.
The current owning record is the verified shop; contact/financial record links
will use composite ownership references when those modules are implemented.

Removing an acting membership with retained history deactivates it and removes
access without deleting its identity. Deleting a user with history deactivates it;
organization owners must transfer ownership first. Deleting an organization with
owned business history deactivates it and clears selected sessions. Restrictive
foreign keys and immutable posted-table triggers prevent cascades from erasing
history, including direct SQL deletion attempts. Future owned models are included
in the shared history-retention check automatically.

## Resource APIs and verification

- `GET/POST /api/v1/shops`: paginated literal-name filtering / creation.
- `GET/PATCH /api/v1/shops/{shop_uuid}`: scoped detail / update or deactivation.
- `GET /api/v1/shops/export`: Unicode CSV, `report:read`, selected tenant only.
- `GET /api/v1/shops/summary`: tenant-scoped aggregate, `report:read`.
- `POST /api/v1/files`, `GET /api/v1/files/{attachment_uuid}`: private shop files.
- `GET /api/v1/audit-logs`: paginated tenant history, `audit:read`.

Shop reads require `settings:read`; creation/updates require `settings:update`.
Schemas reject ownership overrides. CSV exports escape spreadsheet formula prefixes.
Authentication/global user records are not organization-owned business data.

The automated suite tests tenant switching, single-tenant denial, guessed IDs,
foreign shop/references, uploads/downloads, joined/aggregate queries, exports,
permission changes, inactive/recreated memberships, cached responses, concurrent
retry execution, failure rollback and restrictive/immutable database history.
`EMS_TEST_POSTGRES=1 pytest tests/models/test_tenant_postgres.py` additionally tests
the complete upgrade/downgrade chain and immutable triggers in a new PostgreSQL
schema inside a rolled-back transaction; existing application tables are untouched.
