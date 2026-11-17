# Service authorization

Tenant operations must enforce authorization in the service, even when a route
also declares `PermissionRequired[...]`. The route supplies the authenticated
actor; request bodies must never choose `actor_id`, `user_id`, or membership IDs
for authorization.

`TenantOperations.authorize(db, context, *permissions)` reloads the user,
organization and active membership, checks the context's membership identity,
and requires every named permission within that organization. The organization
owner bypass applies only to that organization. Financial operations call
`execute_once`, which performs this check before retry lookup or posting.

Organization and role services require an explicit `actor_id` for protected
entry points. `OrganizationService.authorize` constructs the context from
persisted membership and delegates to `TenantOperations.authorize`. The existing
administration exception lets an active owner with active membership manage an
inactive organization, including reactivation. EMS business operations still
reject inactive organizations.

## Operation permissions

| Service | Entry points and permissions |
| --- | --- |
| Organization | Read: `organizations.read`; update: `organizations.update`; delete: `organizations.delete`; member listing: `organizations.members.read`; membership changes: `organizations.members.manage`. Ownership transfer additionally requires the current owner. |
| Role | Reads: `roles:read`; create/update/delete: the corresponding `roles:*` permission; replacing grants: `roles:permissions:update`; assigning roles: `roles:assign`. Delegates must also hold every permission they grant. Empty replacement lists still require permission to perform the operation. |
| Shop and legacy branch operations | Reads: `settings:read`; changes: `settings:update`; shop exports/counts: `report:read`. |
| Finance | Settings/currencies: `settings:read` or `settings:update`; cashbox reads: `cashbox:read`; cashbox changes: `cashbox:manage`. |
| Ledger | Openings and their reversals: `opening_balance:create` plus owner review; workflow posting: the action permission; journal reads: `report:read`, or `cashbox:read` for cashbox-specific statements. |
| Contact | Reads: `contact:read`; changes: `contact:manage`; restricted identity details additionally require `settings:update`. |
| Contact ledger | Statements/balances: `contact:read`; exports also require `report:read`; openings: `opening_balance:create` plus owner review. |
| Exchange | Rate creation: `settings:update`; rates/history/receipts: `exchange:read`; quotes/posting: `exchange:create`; reversal: `exchange:reverse` plus owner review. |
| Payment | Reads/receipts: `payment:read`; posting: `payment:create`; reversal: `payment:reverse` plus owner review. |
| Customer obligations | Outstanding balances: `contact:read`. |
| Hawala | Reads/receipts: `hawala:read`; draft creation/editing: `hawala:create`; transitions: `hawala:confirm`, `hawala:pay`, `hawala:cancel`, or `hawala:refund`. Correction review uses `hawala:cancel` and the existing owner policy. |
| Agent settlement | Outstanding balances/history: `contact:read`; posting: `agent_settlement:create`; reversal: `agent_settlement:reverse` plus owner review. |
| Daily operations | Category/history reads: `report:read`; category changes: `settings:update`; expenses/transfers and reversals: corresponding `expense:*` or `cash_transfer:*` permissions. Reversals require the owner. |
| Closing | Reads/exports: `report:read`; opening a day and drafting a closing: `day_closing:create`; confirmation: `day_closing:approve` plus owner approval. |
| Reports | `report:read`; audit reports additionally require `audit:read`. |
| Tenant operations | Audit log: `audit:read`; uploads: `attachment:manage`; downloads: `attachment:read`. Contact evidence additionally requires `contact:read` and `settings:update`; expense evidence requires `report:read`. Jobs and caches reauthorize their declared permissions. |

Authorization does not replace ownership checks. Tenant queries must filter by
organization, and shop-specific operations must validate the requested shop and
references. A permission in organization A never grants that permission in B.

## Internal and non-tenant services

`OrganizationContext` and `PermissionService` implement context resolution,
scoped lookups and grant storage. Private organization/role lookups are internal
primitives, not authorized public entry points. Permission registration and
default-role provisioning are trusted startup/bootstrap operations.

Financial serializers, account/obligation constructors, `HawalaPostingService`,
ledger `_write`, and closing calculation helpers run beneath the authorized
workflow boundary. They must not be exposed as standalone HTTP/job operations.
A plain organization context or a membership-only check is not an action grant.
`HistoryService` is an internal retention check used by authorized deletion
workflows.

Organization creation and join requests precede membership and use authenticated
user identity rather than permissions in an existing tenant. Organization
selection validates membership. User profile and session operations derive the
user from authentication. Auth, JWT, password reset, email verification, rate
limiting, security events, and two-factor services use their identity/token
contracts rather than tenant permissions. Mail, Jinja, URL, datetime, financial
value and user-agent utilities do not authorize tenant actions.

## Regression coverage

`tests/services/test_service_authorization.py` calls services directly, without
HTTP dependencies. It covers organization/role permission denial and foreign
membership, action-specific grants, ownership transfer, revoked membership and
inactive actors, EMS reads, and denied financial posting without journal/retry
side effects. Existing API and domain tests cover resource isolation, retry
ownership, grants and revocation, and authorized workflows. SQLite tests do not
replace the opt-in PostgreSQL integration tests.
