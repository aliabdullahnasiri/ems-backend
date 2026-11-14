# Phase 10 — Hawala and agent settlements

All routes use `/api/v1`, an authenticated active membership and the selected
organization. Transfer/settlement routes take `?shop_id=<integer>`; path IDs are
UUIDs, while body references use returned integer IDs. Agent contacts, currencies,
cashboxes, obligations and journals must belong to that organization/shop.

These endpoints record **manual operations**. They do not transmit a partner
instruction, initiate a bank transfer, notify an agent, or verify a beneficiary
through an external identity provider. Operators record their checks and evidence.
An evidence reference is a private database string, not an attachment download
or permission to access another record's files.

## Transfer API and states

| Endpoint | Permission / behavior |
| --- | --- |
| `POST /hawalas` | `hawala:create`; create a draft with a unique `HW-` reference |
| `GET /hawalas` | `hawala:read`; shop history with pagination and filters |
| `GET /hawalas/{uuid}` | `hawala:read`; receipt, payout/refund history, agent obligations |
| `PATCH /hawalas/{uuid}` | `hawala:create`; supplied draft fields only; direction is fixed |
| `POST /hawalas/{uuid}/confirm` | `hawala:confirm`; funding or verified incoming instruction |
| `POST /hawalas/{uuid}/pay` | `hawala:pay`; verified local/partner payout |
| `POST /hawalas/{uuid}/cancel` | `hawala:cancel`; unpaid transfers only; refund due, no cash movement |
| `POST /hawalas/{uuid}/refund` | `hawala:refund`; actual full/partial outgoing refund |
| `GET /hawalas/{uuid}/receipt` | `hawala:read`; original confirmation snapshot; draft returns 409 |
| `POST /hawalas/{uuid}/reviews` | `hawala:cancel` and current owner; paid/cancelled dispute assessment |

Draft creation/editing does not post journals. Draft references are validated at
creation; sender, recipient, destination and complete money terms are required at
confirmation. Confirmation freezes all transfer terms with database protection.
Lifecycle history is append-only, including every draft version. Transfers cannot
be deleted. State is derived from retained one-time transition events:

- `draft → pending` on confirmation.
- `pending → paid` on payout.
- `draft/pending → cancelled` on cancellation.
- Refund and owner review append history without changing those terminal states.

Payout state is distinct from `settlement_status` (`none`, `outstanding`, `partial`,
`settled`). Paying the beneficiary does not silently settle the agent. An agent
can be funded/reimbursed separately before or after payout. Cancellation is blocked
while any transfer obligation has a live settlement; reverse those settlements
first. `GET /hawalas` supports `direction`, `agent_id`, reference substring,
inclusive Kabul `date_from`/`date_to`, `offset=0` and `limit=50` (maximum 100).

## Terms, units and fee policy

Example outgoing draft, using illustrative amounts rather than a market quote:

```json
{
  "direction": "outgoing",
  "agent_id": 4,
  "collection_currency_id": 1,
  "payout_currency_id": 1,
  "cashbox_id": 2,
  "sender_name": "احمد",
  "recipient_name": "محمود",
  "destination": "کابل",
  "collection_amount": "1000.00",
  "payout_amount": "1000.00",
  "rate": "1",
  "customer_fee": "100.00",
  "agent_fee": "20.00",
  "customer_fee_earned_at": "paid",
  "agent_fee_earned_at": "paid",
  "customer_fee_refundable": true,
  "agent_fee_refundable": true
}
```

Rate direction is explicitly `payout_per_collection`. Payout must equal collection
principal multiplied by rate, rounded half-up to payout-currency precision. A
same-currency pair requires rate one and equal principal. Amounts/rates are strict
decimal strings with shared numeral normalization; floats, signs, exponents,
nonpositive principal/rates and excess input precision are rejected. Rates have
up to eight fractional places. Receipts preserve raw multiplication, rounding,
original amounts and both currency codes.

Foreign units require explicit `collection_afn_rate` and/or `payout_afn_rate` (AFN
per corresponding unit). AFN values itself at one. No market lookup or inferred
rial/toman conversion occurs. Original-currency journal totals and AFN book values
balance separately. Principal, customer fee and agent fee remain separate amounts.
Customer fee uses collection currency; agent fee uses payout currency.

Fee earning can be `confirm` or `paid`, independently for each fee. Defaults are
`paid` and refundable. Unearned customer fees use a deferred liability; unearned
agent fees use a prepaid clearing account. Payout reclassifies these to fee income
and fee expense. On cancellation, refundable fees are unwound. Nonrefundable fees
become earned/owed at cancellation, including fees deferred until payout. The
confirmed receipt preserves the agreed policy; later edits or profile changes do
not alter it.

## Outgoing funding and manual partner payout

Confirmation (`{"reason":"Reviewed funding"}`) collects collection principal plus
customer fee into the chosen local cashbox and records agent principal/fee
payables in payout currency. Currency clearing accounts preserve separate original
units. Any difference between principal AFN values is recorded as exchange
spread income/expense. Principal is never classified as commission revenue.

Outgoing payout requires `mode: "partner"`, no `cashbox_id`, literal
`beneficiary_verified: true`, nonblank `verification_reference`, `evidence_reference`
and `reason`. Its event records operator and UTC time. It confirms the partner's
manual payout; **it does not reduce local cash**. Only deferred fee reclassification
may create a journal. A zero-fee partner payout is a retained event without a
money journal. Agent payables remain until explicit allocated settlement.

## Incoming verification and local payout

Incoming collection terms describe money collected by the partner, not cash
collected locally. Confirmation requires literal `instruction_verified: true`,
a nonblank `instruction_reference` and reason. It creates an agent receivable and
beneficiary payable for the local payout. Customer fee is an additional agent
receivable in collection currency; agent fee is a separate payable in payout
currency. Confirmation never changes physical local cash.

Incoming payout requires `mode: "local"`, an active local cashbox, verified
beneficiary/evidence references and reason. It releases the beneficiary payable
and pays cash once. Payout cash uses weighted-average AFN carrying cost; any
historical-book difference is realized separately, using the Phase 9 foreign
clearing book adjustment contract. Insufficient money fails without partial
postings or state changes. A partner-only incoming payout or a local outgoing
payout is rejected; these directions have distinct operational meanings.

## Cancellation, refunds and owner review

Cancelling a pending outgoing transfer requires literal `partner_not_paid: true`
and `partner_confirmation_reference`. Normal cancellation after payout returns
409. Paid/disputed corrections go through the current owner's review endpoint,
with `owner_approved: true`, reason, evidence and `decision: "investigate"` or
`"resolved_offline"`. This records an assessment/correction history; it does not
pretend a paid beneficiary has returned cash or perform an automatic reversal.
Any subsequent financial correction requires a reviewed real operation.

Pending cancellation creates a journal linked to the original confirmation. It
voids principal and refundable fee obligations; nonrefundable agent/customer fees
remain visible as appropriate. Incoming cancellation creates no local refund.
Outgoing cancellation creates a refund payable for collection principal plus any
refundable customer fee. **Cash is unchanged by cancellation**, even if collected
funds have already been spent. Refund due, paid and remaining are returned
separately in collection currency.

An actual refund requires `cashbox_id`, positive `amount`, evidence and reason.
Each posting releases part/all of the stored refund liability and spends only that
cash. Partial refunds release proportional remaining book value; final refund
removes the exact remainder. Total refunds cannot exceed the stored due amount.
Unavailable cash blocks payment without changing refund state. Repeated requests
with the same retry key replay; a second full refund with a new key returns 409.

## Agent settlement API and statements

| Endpoint | Permission / behavior |
| --- | --- |
| `POST /agent-settlements` | `agent_settlement:create`; same-currency allocations |
| `GET /agent-settlements` | `contact:read`; pagination, agent and Kabul date filters |
| `GET /agent-settlements/{uuid}` | `contact:read`; immutable evidence/receipt and reversal status |
| `POST /agent-settlements/{uuid}/reverse` | `agent_settlement:reverse` plus current owner approval |
| `GET /contacts/{uuid}/agent-outstanding` | `contact:read`; remaining principal, fees and openings |
| `GET /agent-settlements/statement?agent_uuid=...` | `contact:read`; agent-only ledger statement |
| Same statement with `format=csv` | Also `report:read`; complete date-filtered CSV export |

Existing `GET /contacts/{uuid}/balances` exposes gross agent receivables and
payables separately by currency. Agent statements reuse the sealed contact ledger,
including openings, transfer obligations, cancellations, settlements and reversals.
They filter role `agent`, even for a contact who is also a customer. CSV exports
include full opening/movement/closing totals, Unicode text and safe spreadsheet
cells; JSON movements use bounded pagination. Both accept inclusive Kabul dates.
Outstanding supports `offset`, `limit` and `include_settled=true` for full history.

A settlement specifies agent, currency, `direction: "receive"|"pay"`, method,
amount, reason, external reference, evidence reference and 1–50 unique obligation
allocations. Allocation sums must equal the declared amount. Direction must match
receivable/payable and every obligation must match the same agent, shop and
currency. Over-allocation, wrong directions, cross-currency netting, foreign IDs
and cancelled obligations are rejected. Partial/final settlements preserve exact
remaining principal and AFN book value. Phase 7 reviewed agent opening debts are
backfilled as allocatable obligations without reposting their money.

`method: "cash"` requires an active cashbox and changes local cash. `bank`/`other`
require no cashbox: they record a **manually completed external settlement** against
an external clearing account, with no local cash movement or bank integration.
Paying cash uses weighted-average cost; receipt cost differences are recognized
separately. Settlements preserve operator, time, original reference, evidence,
amount/currency, allocations and shop/agent receipt details.

Reversal requires `owner_approved: true`, reason and a new retry key. It posts exact
opposite journal legs through a unique linked reversal. Allocations remain retained;
a reversed settlement no longer reduces outstanding debt. Spent cash/incompatible
inventory cost may block a cash reversal. Original settlement evidence is unchanged.

## Transactions and validation

All confirm/pay/cancel/refund/review/settlement/reversal actions require an
`Idempotency-Key` header. Same actor, operation, key and payload replays the saved
posting-time response; changed payload returns 409. Use GET for current state.
The shared organization lock serializes cash, transitions, refunds and allocations.
Journal, event/evidence, obligation/void, allocations, audit and retry result share
one savepoint. A failure leaves no partial movement, balance or state change.

Migration `fae005a4b650` follows Phase 9. It adds the transfer/lifecycle/refund and
agent obligation/settlement tables, composite tenant/shop/actor references, unique
transition/reversal links, retained history triggers, frozen confirmed/cancelled
drafts and agent opening backfill. Tests exercise upgrades/downgrades/re-upgrades,
foreign keys, retained histories, fees, directions, cash effects, refunds, partial
settlements, retries, permission/isolation rules and races on separate connections.
SQLite tests do not establish production PostgreSQL/MySQL concurrency behavior.
