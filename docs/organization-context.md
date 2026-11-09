# Organization context

Run `alembic upgrade head` before starting the updated application. The migration
backfills existing users as active. New organizations receive protected `owner`,
`cashier`, and `accountant` role definitions; startup provisions missing defaults
for existing organizations. Provisioning is repeatable and preserves existing
roles with those names. Staff receive no grants until explicitly assigned a role
through the existing organization member-role API. The organization owner retains
all permissions through the existing owner policy and must have active membership.

Select an organization with
`POST /api/v1/auth/organizations/{organization_uuid}/select`.
`GET /api/v1/auth/context` returns the verified organization, acting membership,
user ID and effective permissions. Missing selection or an inactive organization
returns 403; a missing/inactive membership returns 404; an inactive user or invalid
access token returns 401. Tenant IDs in headers or query parameters do not select
an organization. Organization-management URL routes retain their explicit URL
scope and allow the owner to manage an inactive organization; EMS context denies
all access to an inactive organization.

Use `CurrentOrganization`, `CurrentMembership`, `CurrentUserPermissions`, and
`CurrentOrganizationContext` on EMS routes. Use `PermissionRequired["exchange:create"]`
(or the corresponding action) on each protected operation. Never authorize from
`User.permissions`, which aggregates grants across organizations. Permission cache
keys include organization ID, RBAC version and user ID. Role changes, assignment
changes, membership removal and membership status changes invalidate that version.
Every request still validates membership before using cached permissions.

`OrganizationContext.query(Model)` scopes reads; `get` validates a referenced record
in that scope; `new` assigns ownership and rejects a client-supplied ownership
field. `shop` validates the branch belongs to the organization and is active.
Services must additionally compare each referenced record's shop ID where the
record is shop-owned. Use the scoped query as the input to joins, aggregates,
exports and downloads, including the joined records' organization/shop predicates.
A helper does not automatically scope arbitrary SQL statements.

For queued EMS work, persist organization ID, user ID and membership ID and call
`OrganizationContext.resolve` again at execution. Verify its membership ID matches
the queued actor before proceeding, then check the operation's permissions. Use
`key` to namespace cache/retry identifiers and include organization ID in the
persisted retry unique constraint. Cache final responses only with their tenant
identity. `audit_values` supplies organization, membership and user IDs for events.

Cashier and accountant defaults cannot reverse/refund transactions, approve a
closing, update settings or administer staff. The owner can assign more specific
custom roles through the existing delegation checks.

## Later-module requirements

Financial tables, contacts, cashboxes, attachments, reports and financial jobs do
not exist yet. Their schema constraints and end-to-end isolation tests belong to
Phases 6–12. Each must use a non-null organization foreign key and tenant-scoped
uniqueness; shop references must also be constrained to the same organization.
Posted financial entries and their actor/reference records need restrictive foreign
keys and soft-deactivation policies, never cascading deletion. Financial/audit event
columns must persist `audit_values`; private downloads and retries must revalidate
access even on cache hits. These requirements remain unchecked in PLAN.md until
those modules exist and are tested. Existing auth security events are global login
history, not financial audit events.
