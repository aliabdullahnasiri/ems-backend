# Expenses, cash transfers and daily closing

Phase 11 uses the same selected-organization context, verified `shop_id`, transaction,
tenant lock and exact decimal ledger as the earlier financial workflows. Apply
Alembic revision `fbf006b5c760` before running the updated application.

## API and permissions

All routes are under `/api/v1`. Path identifiers are UUIDs; payload references and
`shop_id` are integer IDs. Responses use the `data` envelope. List routes accept
`offset` and `limit` (1–100). Money is decimal text with the configured currency
precision, including supported Persian/Arabic numerals; floats are rejected.

| Routes | Permission |
| --- | --- |
| GET `/expense-categories`, `/expenses`, `/cash-transfers` | `report:read` |
| POST `/expense-categories`, PATCH `/expense-categories/{id}` | `settings:update` |
| POST `/expenses` | `expense:create` |
| POST `/expenses/{id}/reverse` | `expense:reverse` and current organization owner |
| POST `/cash-transfers` | `cash_transfer:create` |
| POST `/cash-transfers/{id}/reverse` | `cash_transfer:reverse` and current owner |
| POST `/business-days/open`, `/day-closings` | `day_closing:create` |
| GET `/business-days/current`, `/day-closings`, `/day-closings/{id}`, `/day-closings/{id}/export` | `report:read` |
| POST `/day-closings/{id}/confirm` | `day_closing:approve` and current owner |

Financial writes, day opening, snapshot creation and confirmation require an
`Idempotency-Key` header. Identical retries by the same membership replay the
original response, including after closing. Changed payloads conflict; a new key
cannot repeat a reversal, day opening or closing. Configuration category writes
are not financial postings. Errors leave no partial journal, business record,
audit event or financial retry.

## Expenses and transfers

Create an expense category with `name` and optional `is_active`; salary is an
ordinary category, with no payroll calculation. Expenses require `category_id`,
`cashbox_id`, `currency_id`, `amount`, `expense_date`, `payee`, and `explanation`.
The expense date must be today's Kabul date. Backdating is deliberately rejected;
corrections use a linked reversal in an open period. Historical categories and
posted records are retained when a category is deactivated.

Upload optional evidence using the existing private `/files` API, then supply its
`attachment_id` when posting the expense. Linking requires `attachment:manage`
and same-shop ownership. Download requires `attachment:read` and `report:read`.
Contact identity evidence cannot be repurposed as expense evidence (or vice versa).
Files are immutable and retained; existing size, signature and type rules apply.

A transfer requires `source_cashbox_id`, `destination_cashbox_id`, `currency_id`,
`amount`, and `reason`. The cashboxes must be distinct, active and in the selected
shop. One currency applies to both sides; currency conversion requires a separate
exchange. Cash carrying value moves at the source's weighted-average AFN cost,
with exact full-balance exhaustion. Transfers change neither total cash nor profit.
Expenses debit the category expense account and credit cash at carrying cost.

Reversal requests supply `reason`. The opposite journal links to the original;
list responses expose `reversal_entry_id`. Insufficient cash or inconsistent
remaining carrying value rejects a reversal instead of silently changing costs.
Original financial values and their evidence links are immutable.

## Business-day lifecycle

`POST /business-days/open` accepts `day` as an ISO date, which must equal today's
Kabul date. Opening stores the current ledger balances without posting new money.
A later day can open only after the previous day is confirmed closed. It must be
a later actual calendar date; skipped non-working days are allowed. `/current`
returns the latest day with `open`, `closing`, or `closed` status, or null before
first use.

Existing installations continue posting until their first explicit business-day
opening. After that, every ledger posting checks the business-day state under the
same tenant lock, including exchanges, payments, hawalas, settlements, opening
balances, expenses, transfers and reversals. An old open day blocks new-day
posting until it is closed and the current day is opened. Closing the previous
day after midnight is supported. Once closed, a day cannot be reopened or backdated.

To close, submit `counts`, one `{cashbox_id, currency_id, counted}` per existing
cash account, including inactive historical accounts and zero balances. Additional
valid box/currency pairs may be counted for newly discovered cash. Duplicate or
missing counts are rejected. At most 100 pairs are accepted. All differences are
`counted - expected`, independently per currency; they are never netted across
currencies. A shortage uses proportional carrying cost. A foreign-currency overage
requires `overage_book_afn`; AFN overage cost equals its amount. A nonzero difference
requires a nonblank `reason`.

The server captures the cutoff and expected balances under the posting lock.
Creating the immutable snapshot immediately freezes further postings for that
shop. Review the counts before submitting: there is no draft-edit or cancellation
endpoint. Any subsequent correction is recorded after confirmation in a later
open period, with a linked expense/transfer reversal where applicable.

Confirmation requires literal boolean `owner_approved: true` and the actual current
organization owner, in addition to permission. It rechecks duplicate confirmation
and reasons, then posts shortages to `cash_shortage` expense and overages to
`cash_overage` income, with the cash counterpart. A separate immutable confirmation
links the adjustment journal to the snapshot. Zero-difference closing creates no
journal. Expected and counted values are never overwritten. Adjustments are posted
at confirmation time; the snapshot retains its earlier count cutoff and pre-adjustment
expected values. The next day's carried balances include the adjustment exactly once.

The report and CSV export retain per-box/currency expected, counted, difference and
AFN book values. CSV excludes user-supplied free text. Approval identity and reason
are retained through the confirmation, journal and protected audit history.

## Verification

The full regression suite passed with 352 passed and 1 skipped. After moving the
closed-day expense rejection ahead of account setup, all seven Phase 11 tests
passed with SQLAlchemy warnings treated as errors. Ruff checks on changed Python
files, the full Pyright check and whitespace checks passed. Migration tests cover
SQLite upgrade, metadata equivalence, downgrade and re-upgrade with foreign keys
enabled. The concurrency test uses independent SQLite connections. PostgreSQL
and MySQL execution were not exercised in this session; their configured
READ COMMITTED isolation remains required.
