# Dashboard and financial reports

All routes require an active selected organization, shop access, and `report:read`.
Owners and the default accountant role have access; the cashier role does not.
`/reports/audit` additionally requires `audit:read`. The existing `/audit-logs`
endpoint provides the full organization audit trail; this report includes shop
journal audit events.

Routes: `/dashboard`, `/reports/daily`, `/reports/cash`, `/reports/outstanding`,
`/reports/profit`, `/reports/hawala`, `/reports/audit` (under `/api/v1`).

Required query: `shop_id`. Optional: `start`, `cutoff` (timezone-aware instants),
`day` (Kabul calendar date), `closing_id`, `cashier_id` (user ID), `currency_id`,
`cashbox_id`, `contact_id`, `format=json|csv`. A day supplies its Kabul midnight
interval; a closing supplies its retained cutoff. Outstanding and dashboard
balances include all preceding postings; activity reports honor start.
Dashboard profit and volume honor start while balance totals remain cumulative.
Cash with start is a cash movement report; omit start for available balances.

JSON uses the standard data envelope, with shop name, generation time, effective
cutoff, filters, original-currency ledger rows, currency totals, and historical
AFN book values. CSV includes the same metadata, detail, hawala status and audit
sections. Spreadsheet formula-bearing cells are escaped. Decimal strings must
be parsed as decimals, never binary floating point. Debit amounts are positive;
credit amounts negative. Receivable/expense totals are debit-positive;
payable/income totals are credit-positive. Customer and agent directions remain
separate. Cashbox and contact-specific totals accompany currency aggregates.
Hawala statuses and refunds are reconstructed from retained events at cutoff,
not the current draft state. Pending means confirmed and awaiting payout.

## Valuation and profit

Reports read only sealed journal postings. Financial posting workflows already
compute and store weighted-average cash cost per cashbox and currency. Opening
holdings carry the owner-approved AFN book value. A partial disposal removes
rounded proportional cost; full disposal removes the entire remaining cost,
avoiding residual cents. Purchases add their recorded AFN acquisition cost.
Reversals negate original amounts and book values on the reversal posting date;
reports never recost the original transaction using today's rates.

Net realized posted profit is the negative sum of income and expense AFN book
legs. Equity openings, customer principal, hawala principal, cash transfers,
agent settlement principal, and clearing accounts are excluded. Recognized
hawala customer fees and agent fees follow their configured confirm/payout
recognition policy; deferred fees are excluded until earned. Non-AFN operating
expenses use disposed cash's weighted-average book cost; non-AFN hawala fees
use the agreed AFN valuation recorded at recognition. Rate differences realized
by payment/settlement workflows are recognized through their posted spread
legs. There is no market-rate revaluation or unrealized gain estimate.

Each row carries original currency, AFN book value, and an implied historical
conversion rate (book value divided by units). Zero-unit book adjustments have
no implied rate. This rate is a valuation disclosure, not a current quote.
Currency totals must never be summed across units. Filters select ledger legs;
contact filters on exchanges also select their associated exchange entry.

Retained closing cutoffs and immutable postings reproduce historical financial
amounts after subsequent entries and reversals. Shop names and display metadata
are current labels, not a legally archived receipt snapshot. Full journal detail
is returned; large histories currently require narrow dates/filters.

## Examples requiring owner/accountant acceptance

Automated regression examples verify: opening USD 100 at AFN 7,000; buying
USD 100 for AFN 7,000; selling USD 10 for AFN 710 disposes AFN 700 cost and earns
AFN 10; reversing the sale removes AFN 10 profit without changing the earlier
cutoff report. A same-currency hawala with AFN 100 earned customer fee and
AFN 20 agent fee earns AFN 80, excluding AFN 1,000 principal. Existing exchange,
hawala and daily workflow tests cover book-cost expenses and reversal posting.

Owner/accountant review of these accounting policies and examples is still
required. Automated tests are not evidence of that human acceptance.
