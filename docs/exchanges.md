# Phase 8 — Currency exchange and receipts

All endpoints use `/api/v1`, the verified selected organization and an active
`?shop_id=<integer>`. Path identifiers are exchange UUIDs; body references are
integer IDs returned by currency, cashbox and contact APIs. The contact is optional
for walk-in cash exchanges; when supplied it must be an active customer of this shop.

| Endpoint | Permission / behavior |
| --- | --- |
| `POST /rates` | `settings:update`; append manual buying and selling rates |
| `GET /rates` | `exchange:read`; attributed history, newest first |
| `POST /exchanges/quote` | `exchange:create`; nonbinding calculation, no writes |
| `POST /exchanges` | `exchange:create`; fully paid exchange and receipt in one transaction |
| `GET /exchanges` | `exchange:read`; search shop history |
| `GET /exchanges/{uuid}` | `exchange:read`; original details and reversal entry ID |
| `GET /exchanges/{uuid}/receipt` | `exchange:read`; unchanged original receipt snapshot |
| `POST /exchanges/{uuid}/reverse` | `exchange:reverse` and current organization owner; explicit approval and reason |

Lists use `offset=0`, `limit=50` (maximum 100), and inclusive Kabul
`date_from`/`date_to` filters. Rates can filter `base_currency_id` and
`quote_currency_id`. Exchanges can filter a reference substring, `cashier_id`,
`contact_id`, or either leg's `currency_id`. Unmatched filters return empty lists;
foreign currency/contact references and foreign shops return 404.

## Rate and amount direction

Every pair specifies `base_currency_id` and `quote_currency_id`. The rate is always
**quote units per one base unit** (`direction: "quote_per_base"`). `side: "buy"`
means the shop receives base currency and pays quote currency; `side: "sell"`
means it pays base currency and receives quote currency. `amount` is always the
base amount. Rate history stores both `buying_rate` and `selling_rate`, who entered
them, and when. History is append-only. An agreed transaction rate is supplied
explicitly and may differ from advertised rates; changing a rate never changes an
exchange. Nothing fetches market prices automatically.

Example quote body (illustrative historical numbers, **not current market rates**):

```json
{
  "cashbox_id": 1,
  "contact_id": 4,
  "base_currency_id": 2,
  "quote_currency_id": 1,
  "side": "buy",
  "amount": "100.00",
  "rate": "70"
}
```

With USD as base and AFN as quote, the shop receives 100 USD and pays 7,000 AFN.
For posting, add a nonblank `reason` and `Idempotency-Key` header. The service
revalidates and recalculates under the shared organization lock; a quote reserves
no money and can become unaffordable before posting. Both directions use the
same cashbox. Partial/credit settlement belongs to Phase 9.

Amounts and rates are strict decimal strings, with supported Persian/Arabic
numerals normalized by the shared financial parser. Rates have at most eight
fractional places, amounts the configured currency precision. Reject zero or
negative values, identical currencies, exponent notation, floats, overflows,
excess input precision and payouts rounding to zero. Calculated quote amounts
round half-up to quote-currency precision. Responses save the raw product, rounded
amount, signed `rounding_adjustment = rounded - raw`, direction and rounding mode.
No fee is charged in Phase 8: receipts explicitly return `fees: "0.00"` in AFN.

## Cost, profit and journal effects

Outgoing currency carries its cashbox's weighted-average AFN acquisition cost.
A partial payout rounds its proportion of book value to two AFN decimals; paying
all remaining units removes the full remaining book value. Physical cash and
book balances cannot become negative or leave cost attached to zero units.

If AFN is incoming, its received amount is incoming book value. If AFN is outgoing,
its paid amount is the acquired currency's book value. For pairs without AFN,
`incoming_afn_rate` (AFN per incoming currency unit) is required explicitly; it
values the acquisition independently of the agreed pair rate. That field is
rejected for pairs containing AFN. `profit_afn = incoming_book_afn - outgoing_book_afn`.
This realizes a gain/loss on the disposed currency; purchase against AFN has no
immediate profit. AFN must be configured and active for a nonzero gain/loss.

The journal debits incoming cash and credits outgoing cash, with clearing legs for
each original currency. AFN clearing and income/expense legs recognize realized
profit/loss. Every original currency balances separately, total AFN book value
balances, and the net AFN book value of clearing is zero. Selling 10 USD with
700 AFN carrying cost for 710 AFN yields 10 AFN income. Openings remain equity.

The complete exchange, journal seal, receipt, audit and retry result share one
savepoint. A failure leaves none of them. Repeating the same actor/operation/key
and body returns the original response; changed payload returns 409. Permission
and active membership checks still apply to retries. Initial response/retry retains
the original reversal status; GET returns the current separately derived status.

## Receipts, reversal and later payments

Receipts have a unique `EX-<UUID>` reference, UTC posting date, Unicode shop details
and footer, cashier/customer names, currencies, amounts, cost, fees and agreed rate.
These are snapshots protected by database triggers. Subsequent settings, names,
rates or inactive currencies do not rewrite history. Receipt retrieval never posts.
The original receipt remains available after reversal; GET exchange exposes the
linked `reversal_entry_id` separately.

Reversal body: `{"owner_approved": true, "reason": "Correction reason"}` with its
own retry key. Only the current owner with `exchange:reverse` can approve it.
The service negates every original journal leg exactly and creates an immutable
unique reversal link. Insufficient cash or incompatible remaining carrying cost
blocks reversal; this prevents undoing already-spent acquisitions. Cashbox/currency
references must remain active for corrections. A repeated reversal with a new key
returns 409; an identical retry replays the original result.

`exchange_payment_links` is the integration contract for Phase 9: payment services
must register each linked payment journal in the same transaction. Any linked
payment without its own posted linked reversal blocks exchange reversal. Payment
services must restore their allocations before publishing that reversal. This
phase introduces the guard and retained link schema, not credit/payment APIs.

Migration `f8c003e2f430` follows Phase 7 and creates append-only rate, exchange,
reversal and payment-link tables with organization/shop/actor composite foreign
keys, indexes and uniqueness constraints. Upgrade/downgrade/re-upgrade and schema
parity are covered by migration tests; SQLite tests do not establish production
PostgreSQL/MySQL concurrency behavior.
