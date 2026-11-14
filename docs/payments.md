# Phase 9 — Customer debts and payments

All paths have the `/api/v1` prefix and use the authenticated selected organization.
Payment routes require `?shop_id=<integer>`. Resource paths use UUIDs; body references
use returned integer IDs. Customer, currency, cashbox, obligation and exchange
references must belong to the selected organization and expected shop/customer.
Financial writes require `Idempotency-Key` and a nonblank reason. Monetary input
uses exact decimal strings, including the shared Persian/Arabic numeral support.

## Credit exchanges

`POST /exchanges/quote` and `POST /exchanges` accept these additional fields:

| Field | Meaning |
| --- | --- |
| `incoming_cash_amount` | Actual incoming cash, in incoming currency; omitted/null means full amount |
| `outgoing_cash_amount` | Actual outgoing cash, in outgoing currency; omitted/null means full amount |
| `outgoing_afn_rate` | AFN per unpaid outgoing foreign unit; required for a foreign payable |

Both cash amounts may be zero, must respect currency precision, and cannot exceed
the agreed corresponding total. Any unpaid portion requires an active customer.
The original pair, direction, agreed rate, totals, rounding and initial cash
amounts are preserved in the exchange receipt. A quote creates no debt or cash.
Posting revalidates all values under the organization lock.

Unreceived incoming units debit the customer's receivable account; unpaid outgoing
units credit their payable account. Only actual cash changes the physical cashbox.
There is one retained obligation per unpaid direction/currency, linked to the
original exchange, contact, account and sealed journal entry. Fully paid exchanges
continue to work with the Phase 8 request body and retry payload hash.

Incoming AFN value follows the original exchange valuation rules. Incoming cash
gets its proportional AFN book value, and receivables get the exact remainder.
Outgoing cash uses weighted-average inventory cost. Unpaid AFN payables use their
face amount; unpaid foreign payables use the explicitly supplied `outgoing_afn_rate`.
Profit is recognized on the full exchange on an accrual basis, including unpaid
portions. Later payments release those historical carrying values, rather than
recalculating the original exchange using today's rates.

## Outstanding obligations

`GET /contacts/{uuid}/outstanding` requires `contact:read`. Default response contains
unsettled customer receivables and payables; `include_settled=true` includes fully
paid and reversed-source history. `offset=0` and `limit=50` (maximum 100) apply after
filtering. Existing contact balances and statements include the same ledger debt
and settlement entries. Agent openings remain separate from customer settlements.

Each obligation returns its currency, direction, source entry, optional exchange,
original credit principal, paid amount, remaining amount/book value, cancellation
status and all settlement records. Original credit principal is the amount left
unpaid at exchange creation, after any initial cash; the receipt contains the full
trade amounts and initial cash separately. Paid amounts count only live payment
allocations. Reversed settlements stay in history with their reversal entry IDs.
Reversing the source exchange cancels its obligations; it does not fabricate a
customer payment. GET exchange also returns current obligation states separately
from its immutable receipt.

Reviewed customer opening receivables/payables are allocatable too. Migration
`f9d004f3a540` backfills them from sealed Phase 7 customer opening journals without
posting new money, changing balances, or treating openings as income. New customer
opening postings register obligations in their original transaction.

## Payment API

| Endpoint | Permission / behavior |
| --- | --- |
| `POST /payments` | `payment:create`; record received or paid cash and allocations |
| `GET /payments` | `payment:read`; paginated history |
| `GET /payments/{uuid}` | `payment:read`; original receipt and current reversal entry ID |
| `GET /payments/{uuid}/receipt` | `payment:read`; original snapshot, no posting |
| `POST /payments/{uuid}/reverse` | `payment:reverse`, current owner and explicit approval |

Posting/reversal responses also return current ledger balances (`contact:read` is
therefore needed) and all touched obligation states. Retry responses are the
original posting-time snapshot; use GET/outstanding for current status. Payment
lists support `date_from`/`date_to` (inclusive Kabul dates), `contact_id`,
`currency_id`, `direction`, a literal `reference` substring, and bounded pagination.

Example same-currency receipt:

```json
{
  "contact_id": 4,
  "cashbox_id": 1,
  "currency_id": 1,
  "direction": "receive",
  "amount": "6000.00",
  "allocations": [{"obligation_id": 12, "amount": "6000.00"}],
  "reason": "Customer settlement"
}
```

`receive` settles receivables (customer owes shop), and `pay` settles payables
(shop owes customer). Allocations are positive, unique by obligation and limited
to 50 per request. Without conversion, their currencies must equal payment currency
and their sum must exactly equal payment amount. Unallocated advances are not
accepted. Amounts cannot exceed outstanding principal, even across simultaneous
requests. A 10,000 AFN debt with a 6,000 AFN receipt leaves 4,000 AFN outstanding.
Partial settlements release proportional remaining AFN book cost, rounded half-up;
final settlement removes all remaining cost, preventing rounding residue.

Incoming cash receives the released historical receivable book value. Payout cash
uses current weighted-average cashbox carrying cost. A difference from the released
payable book value is realized to AFN income/expense. Balanced AFN/foreign clearing
legs record this difference: foreign clearing permits an AFN-book-only adjustment
with zero original units. The kernel rejects zero-unit cash, zero-unit AFN and
empty zero/zero lines. Both original-currency totals and total AFN book value
remain balanced. Payment entries do not create fictitious foreign cash movements.

## Settlement in a different currency

A different-currency payment must explicitly reference `settlement_exchange_id`.
First post a **fully unpaid exchange** (both cash amounts `"0"`) for this customer
with the desired pair, agreed rate and applicable AFN valuations. This creates two
opposite obligations that act as the conversion bridge. Rates on this exchange are
immutable; the payment receipt embeds its full original receipt.

For example, to receive USD against a customer's AFN receivable, the bridge buys
USD and owes the customer AFN. The USD cash receipt settles the bridge USD
receivable; its AFN payable offsets the selected original AFN receivable. For a
USD payout against an AFN payable, the bridge sells USD: the payment pays its USD
payable and offsets its AFN receivable against the original AFN payable.

```json
{
  "contact_id": 4,
  "cashbox_id": 1,
  "currency_id": 2,
  "direction": "receive",
  "amount": "50.00",
  "settlement_exchange_id": 30,
  "allocations": [{"obligation_id": 12, "amount": "3500.00"}],
  "reason": "USD settlement at linked exchange rate"
}
```

Here the explicitly linked bridge has an illustrative agreed rate of 70 AFN/USD,
not a current market quote. Allocation amounts are in target debt currency. The
service requires the target allocations to equal the linked rate's rounded
conversion and registers three groups of allocations: target debts, bridge cash
leg and bridge offset leg. Only the payment currency moves physically. All target
debts must share the bridge's other currency and cannot belong to the bridge
itself. Partial bridge use follows cumulative rounding; final use exhausts both
legs exactly. A bridge whose legs were settled separately and are out of sync is
rejected. Reverse those separate payments before using it for conversion.

## Immutability, retries and reversal

Payment, receipt, allocations, journal/seal, exchange links, audit and retry result
are saved in one transaction/savepoint. Errors after posting roll back every effect.
Same actor/operation/key and payload replays the original result; changed payload
returns 409. Membership and permission are revalidated, and concurrent postings
share the existing tenant lock. Negative cash or overdrawing debt is never allowed.

Receipts preserve original Unicode customer/shop details, cashier, timestamp,
amount/currency, released books, realized profit, allocations and linked exchange
rate. Retrieval never posts, including after customer or currency deactivation.
Payment reversal requires `{"owner_approved": true, "reason": "Correction"}` and
its own retry key. It posts exact opposite journal legs and a unique retained
reversal record. Allocations remain immutable; their reversed payment no longer
reduces outstanding amounts. Original receipts remain unchanged. Repeated reversal
with another key returns 409. Spent cash or incompatible remaining inventory cost
can block reversal. For a shared conversion bridge, reverse later payments first
to preserve cumulative rounding.

Every allocated exchange is linked through Phase 8's `exchange_payment_links`.
An exchange cannot be reversed while any linked payment is live. Reverse those
payments first; the exchange reversal then cancels its restored original debts.

Tests cover opening backfill, database retention/ownership, 10,000/6,000/4,000
acceptance, receivables/payables, multiple allocations, credit cash effects,
weighted cost/profit, cumulative conversion rounding, rollback, concurrent overpay
and retry attempts, historical receipts, permissions and cross-organization access.
SQLite tests do not prove production PostgreSQL/MySQL concurrency behavior.
