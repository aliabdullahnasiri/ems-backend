# Phase 6 financial foundation

Phase 10 implements manual hawala and agent settlement workflows using this
posting kernel; see [hawalas.md](hawalas.md) for payout, refund and fee contracts.

All paths below have the `/api/v1` prefix and require an authenticated active user,
selected active organization, and active membership. Shop-scoped endpoints require
`?shop_id=<integer>` and validate that shop inside the selected organization.
The organization is never accepted from a financial request body.

## Configuration and API contracts

| Endpoint | Permission / behavior |
| --- | --- |
| `GET /settings` | `settings:read`; name, address, phone, logo reference, language, receipt footer, timezone |
| `PATCH /settings` | `settings:update`; only supplied fields change; optional details accept null; name/language cannot be cleared |
| `GET /currencies` | `settings:read`; includes inactive units |
| `POST /currencies` | `settings:update`; explicit uppercase code and precision, e.g. AFN/2, USD/2, PKR/2 |
| `PATCH /currencies/{id}` | `settings:update`; activate/deactivate only; historical code/precision/unit cannot change |
| `POST /cashboxes` | `cashbox:manage`; creates an empty cashbox |
| `GET /cashboxes` | `cashbox:read`; includes inactive cashboxes and per-currency balances/carrying costs |
| `PATCH /cashboxes/{id}` | `cashbox:manage`; rename or deactivate; history is retained |
| `GET /cashboxes/{id}/movements` | `cashbox:read`; posted journals affecting this box |
| `POST /opening-balances` | `opening_balance:create` **and current organization owner**; explicit `owner_approved: true` and reason |
| `POST /opening-balances/{entry_uuid}/reverse` | Same owner/permission rule; linked correction with a reason |
| `GET /ledger/entries` | `report:read`; posted journal headers, actors, original amounts and AFN book values |
| `GET /audit-logs` | Existing `audit:read` endpoint; protected organization audit history |

Lists use `offset` (default 0) and `limit` (default 50, maximum 100).
Ledger and movement lists support `currency_id` and `day=YYYY-MM-DD` (Kabul day).
Resource path identifiers are UUIDs returned as `uuid`; opening request references
and the `shop_id` query use the returned integer `id` values.
Cashboxes with no account for a currency have an implicit zero balance in that unit;
`balances` contains currencies with ledger accounts. No cash balance field can be
edited. Negative cash is rejected for every operator, including the owner.

Example setup: create AFN, USD and PKR using `POST /currencies` with
`{"code":"USD","precision":2}`; create a cashbox with `{"name":"صندوق نقد"}`.
Currencies are configured explicitly, so shops can choose their supported units.
Additional codes use the same 0–8 decimal precision contract. AFN uses two decimals.
`IRR` always means Iranian rial (`rial_per_unit=1`). `IRT` is an internal explicit
**toman** code (`rial_per_unit=10`), not an ISO currency code. They remain separate
ledger units; no silent rial/toman conversion or automatic rate lookup occurs.

Shop text is Unicode end-to-end; preferred languages are `fa`, `ps`, and `en`.
A missing preference reads as `fa`. A logo reference is metadata, not an upload or
public file-access grant. Private-file authorization remains required to retrieve
any referenced attachment. Use a UTF-8 database (MySQL deployments: utf8mb4).

## Money, time, and rounding

All amounts are decimal **strings**. ASCII, Persian, and Arabic-Indic digits and
the Arabic decimal separator `٫` are supported. Group separators, whitespace,
scientific notation, signs on opening amounts, floats, NaN and excess fractional
precision are rejected. Input is never implicitly rounded. Canonical output uses
ASCII digits, a dot, and the currency's fixed decimal precision. Journal credits
have a minus sign. AFN book amounts always have two decimals.

`financial_values.exact` rounds calculated results using `ROUND_HALF_UP` with a
60-digit working context. Foreign-currency clearing also permits a nonzero AFN book adjustment with zero
original units for realized settlement differences. Cash/AFN zero-unit lines and
zero/zero lines are rejected. See `payments.md` for the paired clearing entries.
Workflow services must calculate using Decimal and
explicitly round only at documented posting boundaries. Original-currency amounts
and AFN carrying values are independent fields; the kernel requires AFN-currency
amounts to equal their book values. It rejects negative cash amounts/carrying value
and residual book cost when cash is reduced to zero. Weighted-average acquisition
cost/profit calculation belongs to exchange/report workflows in Phases 8 and 12;
the kernel stores and reconciles their exact results.

Timestamps are created as UTC instants. SQLite stores their UTC wall-clock value;
responses restore the UTC offset. `business_day` rejects naive input instants;
`day_cutoffs` returns UTC bounds for Kabul midnight through the next midnight.
Queries use `[start, end)`, so midnight is counted once. No client backdating or
opening timestamps are accepted by the Phase 6 endpoints.

## Opening review and retries

Example `POST /opening-balances?shop_id=1`, with `Idempotency-Key: opening-usd-1`:

```json
{
  "cashbox_id": 1,
  "currency_id": 2,
  "amount": "100.00",
  "book_afn": "7000.00",
  "owner_approved": true,
  "reason": "Reviewed opening inventory and documented acquisition cost"
}
```

The 7,000 AFN value is an illustrative historical acquisition cost, not a market
quote. Opening amounts/costs must be positive, with AFN amount equal to AFN cost.
The current owner must submit the reviewed figures; staff cannot approve on the
owner's behalf. The owner, membership, reason, entry reference and UTC timestamp
are retained. One opening is allowed per cashbox/currency, before other activity
in that account. Opening equity is never reported as income. A reversal preserves
that one-time opening key; it does not permit another opening/import.

Financial endpoints require `Idempotency-Key` (1–128 characters). A retry with the
same actor, operation, key and validated payload returns the saved original entry;
a changed payload returns 409. Decimal text spelling is part of the retry payload,
so clients should resend the same body. Keys are organization-scoped. Another
membership cannot replay the result. Missing keys produce validation errors.

The posting kernel locks the stable organization row, revalidates permissions and
references, checks balances, writes all lines, seals the entry, records audit, and
saves the retry response inside one savepoint. The request middleware commits the
outer transaction. Exceptions roll back every financial effect, including account
creation during opening. On connection loss, retry the same request/key. The SQLite
write-lock fallback is exercised on independent connections; PostgreSQL/MySQL use
`SELECT FOR UPDATE` with READ COMMITTED isolation. Application and worker engines
explicitly configure READ COMMITTED for PostgreSQL/MySQL; the kernel rejects other
isolation levels on custom sessions so a pre-lock snapshot cannot authorize stale
cash balances. Each workflow must use the same session and must not commit
inside the posting callback. Configuration changes acquire the same lock.

## Accounting contracts for subsequent workflows

Debits are positive, credits negative. Every journal balances **per original
currency** and across all AFN book values. Each account has a fixed shop, currency,
kind and optional cashbox. Receivables use positive debit balances (owed to shop);
payables use negative credit balances (owed by shop). Never silently net different
currencies. `LedgerService.balance` derives both values from sealed journal lines.
There is no editable balance cache.

| Action | Debit | Credit |
| --- | --- | --- |
| Opening cash | Cash with reviewed original amount/AFN acquisition cost | Opening equity in the same unit/cost |
| Opening receivable (Phase 7) | Contact receivable | Opening equity; cash unchanged |
| Opening payable (Phase 7) | Opening equity | Contact payable; cash unchanged |
| Cash exchange | Incoming currency cash and matching outgoing-currency clearing | Outgoing cash at carrying cost and incoming-currency clearing; realized difference to income/expense |
| Receive customer payment | Cash | Customer receivable |
| Pay customer | Customer payable | Cash |
| Outgoing hawala funding | Cash | Partner/customer principal payable; earned fee separately to income |
| Incoming local hawala payout | Agent receivable (or release existing payable) | Local cash; agent fee separately |
| Partner-confirmed payout | Reclassify the principal obligation as required by transfer state | Corresponding partner obligation; **no local cash leg** |
| Refund actually paid | Refund payable | Cash; cancellation alone establishes refund due |
| Pay agent settlement | Agent payable | Cash when money actually moves locally |
| Receive agent settlement | Cash when locally received | Agent receivable |
| Expense | Expense | Cash |
| Same-unit internal transfer | Destination cashbox | Source cashbox; transfer carrying value, no income |
| Reversal | Exact negation of each original leg | Exact negation of each original leg, linked to original |

For a two-unit exchange, clearing accounts balance original currencies separately;
their AFN book values offset. Example: sell 10 USD costing 700 AFN for 710 AFN:
USD cash -10/-700 book, USD clearing +10/+700; AFN cash +710/+710,
AFN clearing -700/-700, AFN profit income -10/-10. Original currency totals and
book totals are zero; remaining USD carrying cost and 10 AFN realized profit are
preserved. Tests verify this example plus opening, payment, hawala cash/refund,
agent settlement, expense, transfer and opening reversal patterns. These are
foundation contracts; contact allocations, transfer transitions, fee recognition
and workflow-specific reversal APIs remain in their own later phases.

`LedgerService.post` is an internal reusable service, not a public arbitrary-journal
API. The calling workflow supplies its reviewed legs and domain records within its
transaction; the kernel enforces the action permission, organization/shop ownership,
active references, currency precision, balance and cash constraints. Use its retry
boundary for the complete business action. Workflow services can call `_write`
inside their own `TenantOperations.execute_once` callback to include domain records
atomically. They must validate their own state transitions and allocations first.

Posted headers, accounts, lines, seals, retry results and audit rows are protected
against updates/deletes with database triggers. A seal also prevents appending more
lines to an already posted journal. Unsealed journals do not contribute to balances
or API entries. Restrictive composite foreign keys retain ownership and history.
Opening corrections use the owner-only linked reversal endpoint; the unique link
prevents repeated reversal. Insufficient remaining cash blocks reversal. Other
workflow reversals must restore their allocations/state through later services;
the opening reversal service rejects those entries.

## Verification

`tests/services/test_finance.py` verifies exact values, rollback after line insertion,
retries, competing cashier spends/retries on separate connections, reconciliation,
Unicode, permissions/ownership boundaries, inactive cashboxes, immutable history,
Kabul cutoffs and accounting examples. `tests/api/test_finance.py` exercises the HTTP
workflow, selected-organization isolation and cashier restrictions.
`tests/models/test_initial_migration.py` verifies upgrade, downgrade, re-upgrade and
schema parity with foreign keys enabled. Existing opt-in PostgreSQL migration tests
also apply the new revision in a disposable rolled-back schema.
