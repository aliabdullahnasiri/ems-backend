# EMS — Backend Development Plan

**Product:** Exchange Management System for Afghan sarafi businesses. A user can belong to multiple independent organizations. Each organization initially manages one shop; its financial records remain isolated from other organizations.

**Scope:** Backend models and migrations, authentication, users, organizations, roles/permissions, organization context, accounting, APIs, tests, local deployment, and recovery. The EMS backend and database run on a shared local server; core daily operations do not require internet while the server, power, and local network remain available.

**Progress:** Phases 1–5 establish the authentication, organization/RBAC and verified data-ownership foundation. EMS financial workflows continue in Phases 6–13.

**Tracking:** Use `- [x]` for completed work and `- [x]` for remaining work. Financial settings, EMS-specific permissions, and business workflows remain unchecked even where their general platform infrastructure already exists.

## Rules for every phase

- [x] Add the required database models, constraints, indexes, and repeatable migrations as each module is introduced.
- [x] Enforce the authenticated organization, shop, and staff permissions for every query, write, export, and file access.
- [x] Validate referenced records belong to the selected organization and shop; prevent cross-organization references and access by guessed IDs.
- [x] Implement business logic in reusable services shared by API operations, jobs, and tests.
- [x] Return consistent validation errors, pagination, filtering, and exact decimal money values with explicit currency codes.
- [x] Require idempotency keys for financial writes; reject reuse with a different payload and return the original result for an identical retry.
- [x] Use database transactions, locking, and constraints to enforce financial rules under concurrent requests.
- [x] Keep credentials and customer identity documents out of application logs and public storage.
- [x] Add API documentation, including permissions, money/rate conventions, state transitions, and retry behavior.
- [x] Add focused automated tests for the financial and permission rules in each phase.

---

## Phase 1 — Authentication

### `/auth`

#### Basic authentication

- [x] `POST /auth/register`
- [x] `POST /auth/login`
- [x] `POST /auth/refresh`
- [x] `POST /auth/logout`
- [x] `GET /auth/me`

#### Email verification

- [x] `POST /auth/email/verify`
- [x] `POST /auth/email/resend`

#### Password management

- [x] `POST /auth/password/forgot`
- [x] `POST /auth/password/reset`
- [x] `POST /auth/password/change`

#### Sessions

- [x] `GET /auth/sessions`
- [x] `DELETE /auth/sessions/{session_id}`
- [x] `POST /auth/sessions/revoke-all`

#### 2FA

- [x] `POST /auth/2fa/setup`
- [x] `POST /auth/2fa/enable`
- [x] `POST /auth/2fa/disable`
- [x] `POST /auth/2fa/verify`
- [x] `POST /auth/2fa/recovery-codes`

#### OTP

- [x] `POST /auth/otp/setup`
- [x] `POST /auth/otp/enable`
- [x] `POST /auth/otp/disable`
- [x] `POST /auth/otp/verify`

#### Authentication security

- [x] Refresh-token rotation.
- [x] Token revocation.
- [x] Session/device information.
- [x] Login timestamp.
- [x] Last activity.
- [x] IP address tracking.
- [x] User-agent/device tracking.
- [x] Expired-session cleanup.
- [x] Suspicious-login logging.
- [x] Rate limiting for authentication.
- [x] Account lock/rate-limit policy.

---

## Phase 2 — User System

### Users

- [x] User model.
- [x] User schema.
- [x] User service.
- [x] User authentication.
- [x] User profile update.
- [x] Change username.
- [x] Change email.
- [x] Birthday/profile information.
- [x] Account activation/deactivation.
- [x] User deletion/deactivation policy.

### User endpoints

- [x] `GET /users/me`
- [x] `PATCH /users/me`
- [x] `PATCH /users/me/email`
- [x] `PATCH /users/me/username`
- [x] `DELETE /users/me`

### Administration endpoints

- [x] `GET /users`
- [x] `GET /users/{id}`
- [x] `PATCH /users/{id}`
- [x] `PATCH /users/{id}/status`

---

## Phase 3 — Multi-Organization

One user can belong to multiple organizations through separate memberships. Each organization owns its EMS data.

```text
User
  ├── OrganizationMembership → Organization A → EMS Data A
  └── OrganizationMembership → Organization B → EMS Data B
```

### Organization models

- [x] Organization.
- [x] OrganizationMembership.
- [x] Organization settings.
- [x] Organization status.
- [x] Organization owner.
- [x] Organization timestamps.

### Organization endpoints

- [x] `POST /organizations`
- [x] `GET /organizations`
- [x] `GET /organizations/{organization_id}`
- [x] `PATCH /organizations/{organization_id}`
- [x] `DELETE /organizations/{organization_id}`

### Organization membership

- [x] `GET /organizations/{organization_id}/members`
- [x] `POST /organizations/{organization_id}/members`
- [x] `PATCH /organizations/{organization_id}/members/{member_id}`
- [x] `DELETE /organizations/{organization_id}/members/{member_id}`

### Organization switching

- [x] Select the current organization through `POST /auth/select-organization` or `POST /auth/organizations/{organization_id}/select`.
- [x] Include the selected organization in the authenticated context.

---

## Phase 4 — Organization RBAC

Roles and permissions are assigned through organization membership.

### Roles

- [x] Role model.
- [x] Role service.
- [x] Basic role infrastructure.
- [x] Organization-specific roles.
- [x] Role assignment.
- [x] Role update.
- [x] Role deletion.

### Role endpoints

- [x] `GET /organizations/{organization_id}/roles`
- [x] `POST /organizations/{organization_id}/roles`
- [x] `GET /organizations/{organization_id}/roles/{role_id}`
- [x] `PATCH /organizations/{organization_id}/roles/{role_id}`
- [x] `DELETE /organizations/{organization_id}/roles/{role_id}`

### Permissions

- [x] Permission model.
- [x] Permission definitions.
- [x] Permission caching.
- [x] Organization permission assignment.
- [x] Role-permission management.

### Permission endpoints

- [x] `GET /organizations/{organization_id}/permissions`
- [x] `GET /organizations/{organization_id}/roles/{role_id}/permissions`
- [x] `PUT /organizations/{organization_id}/roles/{role_id}/permissions`

---

## Phase 5 — Organization Context

Every EMS operation must know which organization owns its data. Establish and verify this context before building the financial modules.

Implementation: see `docs/organization-context.md`. Organization/shop ownership is enforced for the existing records and through shared ORM, reference-validation, posting, retry, job, cache, file and audit boundaries. Tests exercise these boundaries before any posting callback executes. Contacts, cashboxes, currencies and financial workflows will use these contracts when introduced in Phases 6–12; those later modules are not marked complete.

### Request dependencies

- [x] Implement `CurrentUser` for the authenticated active user.
- [x] Implement `CurrentOrganization` for the selected active organization.
- [x] Implement `CurrentMembership` for the user's active membership in that organization.
- [x] Implement `CurrentUserPermissions` using that membership's organization roles.
- [x] Reject organization-scoped requests without a valid selected organization or active membership.
- [x] Validate the selected organization against the authenticated user's membership; do not trust a client-supplied organization ID alone.
- [x] Enforce organization-specific permissions for each action and refresh/invalidate cached permissions when roles or memberships change.

### Ownership and isolation

- [x] Store `organization_id` on every organization-owned business record; backfill and constrain any existing data before use.
- [x] Apply organization filters to reads, writes, joins, aggregates, statements, reports, exports, files, and audit queries.
- [x] Derive organization ownership from the verified context when creating records.
- [x] Validate that referenced contacts, cashboxes, currencies/settings, financial transactions, and attachments belong to the same organization.
- [x] Apply shop ownership within the selected organization; a shop ID must not replace organization validation.
- [x] Include organization identity in permission/cache keys, financial retry keys, uniqueness constraints, and background-job context.
- [x] Record the organization and acting membership in financial/audit events.
- [x] Protect financial history when an organization, user, membership, or related record is deactivated or deleted; prevent cascading deletion of posted entries.

Every organization-owned business query must effectively include:

```sql
WHERE organization_id = current_organization.id
```

For example, `GET /cashboxes` must return only cashboxes owned by the selected organization. It must not expose another organization's records when an ID, filter, or export option changes.

### EMS permission definitions

- [x] Define EMS-specific permissions using the existing permission infrastructure.
- [x] Assign owner, cashier, and accountant permissions within each organization.
- [x] Keep correction, refund, staff-management, settings, and closing approval actions restricted to their authorized organization roles.

Example EMS permission names to implement:

```text
settings:read
settings:update
cashbox:read
cashbox:manage
opening_balance:create
contact:read
contact:manage
exchange:read
exchange:create
exchange:reverse
payment:read
payment:create
payment:reverse
hawala:read
hawala:create
hawala:confirm
hawala:pay
hawala:cancel
hawala:refund
agent_settlement:create
agent_settlement:reverse
expense:create
expense:reverse
cash_transfer:create
cash_transfer:reverse
day_closing:create
day_closing:approve
report:read
audit:read
```

### Completion checks

- [x] A user belonging only to Organization A cannot read, change, export, or download data from Organization B.
- [x] A user belonging to both organizations receives only the selected organization's data and permissions.
- [x] Removing or deactivating a membership prevents subsequent access to that organization.
- [x] Inactive organizations are rejected according to the organization's access policy.
- [x] Cross-organization record references are rejected before financial posting.
- [x] Jobs, cached responses, attachments, reports, and financial retries cannot leak or reuse another organization's data.

---

## Phase 6 — Shop Settings and Financial Foundation

Implementation and accounting contracts: see `docs/financial-foundation.md`. Later workflow APIs and state/allocation rules remain in their respective phases.

### Settings and data handling

- [x] Store shop name, address, phone, logo reference, preferred language, and receipt footer.
- [x] Store timestamps consistently and calculate business days and report cutoffs in Kabul timezone.
- [x] Store and return Unicode Dari/Pashto text and language preferences without corrupting names or notes.
- [x] Normalize supported numeral input, validate decimal strings, and return consistent exact monetary values.
- [x] Configure AFN, USD, PKR, and additional currencies with explicit codes and decimal precision.
- [x] If Iranian currency is enabled, clearly distinguish rial from toman and store the chosen conversion consistently.

### Cashboxes and accounting

- [x] Create and deactivate cashboxes without losing their history.
- [x] Track each cashbox balance separately for every currency.
- [x] Implement ledger accounts, journal entries, and journal lines using double-entry accounting.
- [x] Store original-currency amounts and the AFN book values required for cost/profit reporting.
- [x] Use exact decimal calculations and documented rounding rules.
- [x] Define and verify the accounting entries for opening balances, exchanges, payments, hawala, settlements, expenses, transfers, and reversals.
- [x] Require balanced entries and derive cash/debt balances from posted ledger records.
- [x] Save each financial action and all its ledger effects in one database transaction.
- [x] Give financial requests a unique retry key so repeating a request returns its original result without posting again.
- [x] Protect cash checks and postings against simultaneous cashier requests.
- [x] Prevent unauthorized negative cash balances and reject invalid amounts/currencies.
- [x] Post reviewed opening balances and foreign-currency acquisition costs once, with owner approval; do not treat openings as income.
- [x] Keep posted financial records immutable and record corrections through linked reversals.
- [x] Record actor, action, record reference, reason, and timestamp in protected audit history.

### Endpoints

- [x] `GET /settings`
- [x] `PATCH /settings`
- [x] `GET /currencies`
- [x] `POST /currencies`
- [x] `PATCH /currencies/{id}`
- [x] `POST /cashboxes`
- [x] `GET /cashboxes`
- [x] `PATCH /cashboxes/{id}`
- [x] `GET /cashboxes/{id}/movements`
- [x] `POST /opening-balances`
- [x] `GET /ledger/entries`
- [x] `GET /audit-logs`

### Completion checks

- [x] A failed financial request leaves no partial transaction or balance change.
- [x] Two simultaneous requests cannot spend the same available cash.
- [x] Repeated requests with the same retry key cannot create duplicate ledger entries.
- [x] Ledger totals reconcile with API balance responses, including openings and reversals.

---

## Phase 7 — Customers and Agents

Implementation: `docs/contacts.md`. Phase 7 uses Phase 6 revision `f6a001c9d210`; the phase completion tracker remains pending until that foundation is integrated.

### Contacts and balances

- [x] Create a contact as a customer, an agent, or both.
- [x] Store name, phone, location, notes, and identification fields required for the agreed workflow.
- [x] Restrict access to identification details and any supporting files.
- [x] Implement private attachment upload/download with file-size/type validation, organization/shop ownership, authorized record linking, and protected storage; preserve files referenced by posted financial records.
- [x] Search contacts by name or phone and flag likely duplicates.
- [x] Update or deactivate contacts while retaining their financial history.
- [x] Record owner-reviewed opening receivables and payables through the ledger.
- [x] Return separate customer/agent balances for every currency.
- [x] Return explicit customer/agent receivable and payable directions so the caller can distinguish who owes whom.
- [x] Compute statement running balances by currency using a documented receivable/payable sign convention.
- [x] Provide date-filtered statement responses and CSV exports with opening balances, movements, and closing balances.

### Endpoints

- [x] `POST /contacts`
- [x] `GET /contacts`
- [x] `GET /contacts/{id}`
- [x] `PATCH /contacts/{id}`
- [x] `GET /contacts/{id}/balances`
- [x] `GET /contacts/{id}/statement`
- [x] `POST /files` — authorized upload linked to a permitted record.
- [x] `GET /files/{id}` — authorized download only.

### Completion checks

- [x] Opening debts change contact balances without changing physical cash or reported income.
- [x] Every customer/agent balance returned by the API can be traced to its ledger entries.

---

## Phase 8 — Currency Exchange and Receipts

Implementation and API/accounting contracts: see `docs/exchanges.md`. Fully paid cash exchanges, immutable receipts and owner-approved reversals use the shared Phase 6 posting/retry boundary. Credit settlement remains in Phase 9.

### Rates and cash exchanges

- [x] Set buying and selling rates from the shop’s perspective.
- [x] Store and return the currency pair and rate direction explicitly; an illustrative rate of 70 AFN per USD is not a current market quote.
- [x] Preserve rate history, record who entered each rate, and expose history through date filters on `GET /rates`.
- [x] Accept customer, cashbox, currencies, amount, and agreed rate; validate ownership, active status, and staff permission.
- [x] Provide a quote endpoint returning exact incoming/outgoing amounts and rounding without changing balances; revalidate and recalculate when posting.
- [x] Validate amounts, currency pairs, precision, and available payout cash.
- [x] Save the agreed rate, direction, calculated amounts, and rounding with the transaction.
- [x] Post both currency movements together with their accounting and cost effects.
- [x] Keep historical transactions unchanged when the rate table changes.
- [x] Support transaction search and filters by date, reference, cashier, customer, and currency.
- [x] Reverse an exchange only with authorized approval, a reason, and a link to the original transaction.
- [x] Preserve original and reversal entries; prevent repeated reversal and handle linked payments before reversal.

### Receipt data

- [x] Generate a unique receipt reference with shop, date, cashier, currencies, amounts, rate, and applicable fees.
- [x] Return receipt data containing the original transaction values and Unicode shop/customer details.
- [x] Repeated receipt retrieval must return the same financial details without posting a new transaction.

### Endpoints

- [x] `POST /rates`
- [x] `GET /rates`
- [x] `POST /exchanges/quote`
- [x] `POST /exchanges`
- [x] `GET /exchanges`
- [x] `GET /exchanges/{id}`
- [x] `GET /exchanges/{id}/receipt`
- [x] `POST /exchanges/{id}/reverse`

### Completion checks

- [x] Buying 100 USD at an illustrative 70 AFN/USD increases USD cash by 100 and decreases AFN cash by 7,000.
- [x] Selling currency, reversing an exchange, changing rates later, and repeated receipt retrieval produce the expected balances/history.

---

## Phase 9 — Customer Debts and Payments

### Credit exchanges and settlement

- [ ] Extend exchanges to support unpaid or partly paid amounts in their original currencies.
- [ ] Change cash balances only for money actually received or paid.
- [ ] Link each unpaid amount to the original exchange and customer.
- [ ] Return original amount, paid amount, remaining amount, currency, and settlement history.
- [ ] Record money received from customers and money paid to customers.
- [ ] Allocate full or partial payments to specific outstanding obligations.
- [ ] Prevent allocation beyond the payment amount or outstanding obligation.
- [ ] Require an explicit linked exchange for settlement in a different currency; preserve its agreed rate.
- [ ] Return immutable payment receipt data and updated statement balances after a successful posting.
- [ ] Reverse a payment through linked entries and restore its allocations/outstanding amounts.

### Endpoints

- [ ] `POST /payments`
- [ ] `GET /payments`
- [ ] `GET /payments/{id}`
- [ ] `GET /payments/{id}/receipt`
- [ ] `GET /contacts/{id}/outstanding`
- [ ] `POST /payments/{id}/reverse`

### Completion checks

- [ ] An obligation of 10,000 AFN followed by a 6,000 AFN payment leaves 4,000 AFN outstanding.
- [ ] Customer receivables, customer payables, partial payment, different-currency settlement, and reversal all reconcile.

---

## Phase 10 — Hawala and Agent Settlements

### Transfer records

- [ ] Support incoming and outgoing hawala as distinct directions.
- [ ] Store sender, recipient, destination, partner agent, and unique transfer reference.
- [ ] Store collection currency/amount, payout currency/amount, agreed rate, customer fee, and agent fee separately.
- [ ] Agree and record when fees are earned and which fees are refundable.
- [ ] Keep draft records free of financial postings; validate required details before confirmation.
- [ ] Support draft, pending, paid, and cancelled states with controlled transitions.
- [ ] Separate customer funding, beneficiary payout, agent obligations/settlement, and earned commission in the ledger.
- [ ] For outgoing transfers, record collected funds and the partner’s payout obligation.
- [ ] For incoming transfers, verify the partner instruction before authorizing a local payout.
- [ ] Track payout status separately from agent settlement status.

### Payout, cancellation, and refund

- [ ] Record beneficiary verification, payout operator, timestamp, and evidence/reference.
- [ ] Distinguish a payout from the shop’s cashbox from manually confirmed payout by a partner; only local cash payouts reduce the shop’s cashbox.
- [ ] Validate cash availability for a local payout and post its effects atomically.
- [ ] Prevent duplicate payout and conflicting payout/cancellation transitions.
- [ ] Require confirmation that a partner has not paid before cancelling an outgoing instruction.
- [ ] Block normal cancellation after payout and route disputed/paid corrections to owner review.
- [ ] Track refund due separately from refund actually paid, using the stored refund policy.
- [ ] Post each refund once, link it to the original transfer, and prevent excess refunds.
- [ ] Return the cancellation, payout, refund, and correction history with authorized transfer detail requests.

### Agent settlements

- [ ] Return agent receivables/payables separately by currency.
- [ ] Link full or partial settlements to outstanding agent obligations.
- [ ] Record settlement method, reference, evidence, and operator; change local cash only when it actually moves.
- [ ] Prevent excess allocation and silent netting across different currencies.
- [ ] Provide transfer receipt data and agent settlement statement responses/exports.

### Endpoints

- [ ] `POST /hawalas`
- [ ] `GET /hawalas`
- [ ] `GET /hawalas/{id}`
- [ ] `PATCH /hawalas/{id}` — draft details only.
- [ ] `POST /hawalas/{id}/confirm`
- [ ] `POST /hawalas/{id}/pay`
- [ ] `POST /hawalas/{id}/cancel`
- [ ] `POST /hawalas/{id}/refund`
- [ ] `GET /hawalas/{id}/receipt`
- [ ] `POST /agent-settlements`
- [ ] `GET /agent-settlements`
- [ ] `GET /agent-settlements/{id}`
- [ ] `POST /agent-settlements/{id}/reverse`

### Completion checks

- [ ] An outgoing transfer can be funded, confirmed paid by its partner, and settled without counting principal as revenue or reducing local cash twice.
- [ ] An incoming transfer can be verified and paid locally with the correct cash and agent balance effects.
- [ ] Cancellation, refund, duplicate payout attempts, and partial agent settlements reconcile correctly.
- [ ] API documentation distinguishes local payouts, manually confirmed partner payouts, and agent settlements; no automatic partner transfer or notification is implied.

---

## Phase 11 — Expenses and Daily Closing

### Expenses and internal transfers

- [ ] Create expense categories and record amount, currency, cashbox, date, payee, and explanation.
- [ ] Record salary payments as expenses without adding a payroll module.
- [ ] Attach expense evidence where needed and support authorized linked reversals.
- [ ] Transfer money between shop cashboxes in the same currency with both sides posted together.
- [ ] Use a separate exchange when an internal movement changes currency.

### Opening and closing the day

- [ ] Open a business day using carried-forward balances without reposting opening entries.
- [ ] Calculate and return expected cash by cashbox and currency at a defined closing cutoff.
- [ ] Accept counted cash separately from the calculated expected cash and retain both values.
- [ ] Calculate and return shortages/overages separately for every currency.
- [ ] Require a reason and owner approval for discrepancies; use explicit adjustment entries rather than overwriting cash balances.
- [ ] Persist an immutable closing snapshot and expose its report data/export.
- [ ] Prevent duplicate closing and block new/backdated postings into closed periods.
- [ ] Record later corrections as linked entries in an open period with an audit trail.

### Endpoints

- [ ] `GET /expense-categories`
- [ ] `POST /expense-categories`
- [ ] `PATCH /expense-categories/{id}`
- [ ] `POST /expenses`
- [ ] `GET /expenses`
- [ ] `POST /expenses/{id}/reverse`
- [ ] `POST /cash-transfers`
- [ ] `GET /cash-transfers`
- [ ] `POST /cash-transfers/{id}/reverse`
- [ ] `POST /business-days/open`
- [ ] `GET /business-days/current`
- [ ] `POST /day-closings`
- [ ] `GET /day-closings`
- [ ] `GET /day-closings/{id}`
- [ ] `POST /day-closings/{id}/confirm`

### Completion checks

- [ ] The closing-confirmation endpoint rejects unexplained or unapproved counted/expected differences.
- [ ] A cash transfer changes the two cashbox balances without changing total shop cash or profit.
- [ ] Closing and opening the next day carry balances forward exactly once.

---

## Phase 12 — Dashboard and Reports

### Dashboard aggregation and financial reports

- [ ] Return cash available by cashbox and currency.
- [ ] Return customer receivables and payables separately.
- [ ] Return agent receivables/payables, pending hawala, and refunds due.
- [ ] Aggregate exchange volume, earned fees, agent fees, and operating expenses for the selected period.
- [ ] Calculate realized exchange profit using a verified weighted-average currency cost method in AFN.
- [ ] Document AFN conversion/book-value treatment for non-AFN fees and expenses.
- [ ] Handle opening currency costs and exchange reversals consistently in the cost calculation.
- [ ] Exclude customer funds, hawala principal, agent settlements, and unrealized gains from realized profit.
- [ ] Return original-currency totals and include the conversion rate, valuation basis, and cutoff for any AFN-equivalent values.
- [ ] Provide daily reports, cash movements, outstanding balances, profit details, and audit history.
- [ ] Add date/cashier/currency/contact filters where relevant.
- [ ] Support JSON responses and CSV exports with shop name, filters, currency information, and generation time.
- [ ] Restrict sensitive financial and audit reports by role.

### Endpoints

- [ ] `GET /dashboard`
- [ ] `GET /reports/daily`
- [ ] `GET /reports/cash`
- [ ] `GET /reports/outstanding`
- [ ] `GET /reports/profit`
- [ ] `GET /reports/hawala`

### Completion checks

- [ ] Report cash and outstanding balances match the ledger for the same cutoff and filters.
- [ ] Verify profit calculations against owner/accountant-reviewed examples covering currency purchases, sales, opening holdings, fees, expenses, and reversals.
- [ ] A closed-day report can be reproduced without later entries silently changing its snapshot.

---

## Phase 13 — Backend Reliability, Deployment, and Acceptance

### Local deployment and operations

- [ ] Install the backend and database on the shop’s shared local server using a documented configuration.
- [ ] Ensure core APIs, authentication, financial posting, and scheduled jobs have no external internet dependency.
- [ ] Protect local network access and credentials, configure service startup, and document restart procedures.
- [ ] Add liveness and readiness checks; readiness verifies database connectivity without exposing secrets or customer information.
- [ ] Add structured error logs and monitoring for failed postings, database errors, and failed backup jobs.
- [ ] Define predictable success/error responses and safe retry behavior after connection loss or timeouts.
- [ ] Document database migrations, pre-update backup, safe application update, and compatible recovery procedures.

### Backup and restoration

- [ ] Schedule encrypted backups of the database, private attachments, and required configuration to a separate location/device.
- [ ] Define backup frequency, retention, verification, responsible person, and failure reporting.
- [ ] Keep backup decryption/recovery instructions and credentials available to the authorized owner.
- [ ] Restore a backup into a separate installation and verify transaction counts, cash balances, contact balances, outstanding hawala, and attachments.
- [ ] Verify committed transactions survive application restart and interrupted requests leave no partial postings.

### API and accounting acceptance tests

- [ ] Verify exchange quote → posted exchange → receipt retrieval → reversal through the API.
- [ ] Verify credit exchange → partial payment → final payment → contact statement.
- [ ] Verify incoming/outgoing hawala, partner confirmation, local payout, cancellation, refund, and agent settlement.
- [ ] Verify expense → cash transfer → counted cash submission → approved closing → next-day opening.
- [ ] Verify concurrent cashier requests, repeated retry keys, and insufficient-cash rejection.
- [ ] Verify duplicate payout/refund prevention and conflicting financial state changes.
- [ ] Verify permissions, session expiry, organization and shop isolation, private attachment access, and audit history.
- [ ] Verify exact calculations and rounding for every supported currency/pair, including non-AFN fees/expenses and reversals.
- [ ] Verify Unicode Dari/Pashto storage, normalized numeral input, receipt payloads, and statement/report exports.
- [ ] Verify closed-period enforcement and reproducible historical closing snapshots.
- [ ] Verify core APIs continue working when the internet is disconnected and the local server/network remain available.
- [ ] Configure the operator-approved identification, recordkeeping, retention, and reporting rules before production data is used.

### Opening data and launch readiness

- [ ] Validate and import reviewed opening cash, customer/agent obligations, and foreign-currency acquisition costs through controlled ledger entries.
- [ ] Run representative API workflows alongside the existing books during an agreed reconciliation period.
- [ ] Compare recorded transactions, cash, customer balances, agent balances, and fees with the reviewed source records.
- [ ] Resolve unexplained differences and rerun the affected backend checks.
- [ ] Obtain owner/accountant acceptance of reconciled financial results and the tested recovery process.
- [ ] Deliver API documentation, installation/configuration instructions, and backup/recovery instructions.

### Operational endpoints

- [ ] `GET /health/live` — minimal liveness response.
- [ ] `GET /health/ready` — minimal readiness response; keep detailed diagnostics protected.

---

## Phase completion tracker

- [x] Phase 1 — Authentication
- [x] Phase 2 — User System
- [x] Phase 3 — Multi-Organization
- [x] Phase 4 — Organization RBAC
- [x] Phase 5 — Organization Context
- [x] Phase 6 — Shop Settings and Financial Foundation
- [ ] Phase 7 — Customers and Agents
- [ ] Phase 8 — Currency Exchange and Receipts
- [ ] Phase 9 — Customer Debts and Payments
- [ ] Phase 10 — Hawala and Agent Settlements
- [ ] Phase 11 — Expenses and Daily Closing
- [ ] Phase 12 — Dashboard and Reports
- [ ] Phase 13 — Backend Reliability, Deployment, and Acceptance
