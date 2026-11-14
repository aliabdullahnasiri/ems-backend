# Customers, agents and their statements (Phase 7)

Phase 7 depends on Phase 6's shared ledger (`f6a001c9d210`). Apply its code and
migration first, then `f7b002d1e320`; this phase does not introduce a second ledger
or maintain mutable contact balance fields. The implementation was checked with
a temporary combined copy of the two worktrees. No changes to the other worktree
are needed to review this phase.

## Contact API

All paths below are relative to `/api/v1`. Organization ownership comes from the
verified selected organization. All contact references are UUIDs. Each contact
belongs to one shop; an organization-owned shop UUID is required at creation and
when listing. References to another organization's contacts, shops or files
return 404. Inactive memberships and organizations cannot access these APIs.

| Operation | Permission | Behavior |
| --- | --- | --- |
| `POST /contacts` | `contact:manage` | Required `shop_uuid`, `name`; `is_customer` defaults true, `is_agent` defaults false; at least one role required. |
| `GET /contacts?shop_uuid=...` | `contact:read` | `search` matches literal normalized names or phone digits, `role=customer\|agent`, `active=true\|false`, `offset`, `limit` (1–100). |
| `GET /contacts/{contact_uuid}` | `contact:read` | Includes inactive contacts so retained history remains available. |
| `PATCH /contacts/{contact_uuid}` | `contact:manage` | Change name, phone, location, notes, identity or active state. Omitted values remain unchanged. Nullable text can be cleared explicitly. |
| `GET /contacts/{contact_uuid}/balances` | `contact:read` | Separate gross receivables/payables per role and currency, plus signed net receivable. |
| `GET /contacts/{contact_uuid}/statement` | `contact:read` | Opening totals, movements with ledger references and running balances, closing totals. |
| `GET /contacts/{contact_uuid}/statement?format=csv` | `contact:read`, `report:read` | Complete Unicode CSV export, independent of movement pagination. |
| `POST /contacts/{contact_uuid}/opening-balances` | `opening_balance:create`, organization owner | Owner-reviewed opening obligation; requires `Idempotency-Key`. |

Optional text fields are `phone` (50 characters), `location` (500), `notes`
(2,000), and `identification` (2,000). Names accept Unicode Dari/Pashto. Exact
normalized names or digit-equivalent phone numbers in the same shop produce up
to 20 `likely_duplicate_uuids`; this is a warning, not a uniqueness constraint.
Phone search recognizes Persian and Arabic numerals and ignores punctuation.

Roles may be added. Existing roles cannot be removed because accounting records
retain their role dimension. Deactivate a contact using `is_active=false` instead;
its balances, statements, identity evidence and original journal records remain.
New obligations and evidence links reject inactive contacts.

## Identity and private evidence

Reading or modifying `identification` additionally requires `settings:update`,
the existing restricted permission not granted to the standard cashier or
accountant roles. Other readers receive no identification value. Identification
is encrypted using the application's Fernet encryption facility; protect and
retain its configured encryption key with backups. Identity contents and file
bytes are never placed in audit events or financial retry payloads.

`POST /files` accepts multipart `shop_uuid`, optional `contact_uuid`, and `file`.
A contact link additionally requires `contact:manage`, `contact:read` and
`settings:update`; the contact, file and shop must belong to the same organization
and shop. Unlinked shop uploads remain supported for compatibility. Contact
identity uploads must include `contact_uuid` so the identity access boundary is
retained. Upload and linking succeed or roll back together.

The existing size limit is 5 MiB, with PDF, PNG and JPEG MIME/signature validation.
Names cannot contain directory traversal, CR/LF or NUL. Storage is private DB
binary data with no public URL. A file is downloaded only through
`GET /files/{attachment_uuid}`, which revalidates membership, organization and
shop and requires `attachment:read`. A contact-linked file also requires
`contact:read` and `settings:update`. Download responses include `no-store`,
`nosniff`, and an encoded attachment filename.

MySQL storage is widened to MEDIUMBLOB to support the complete 5 MiB limit;
downgrading this phase retains that wider storage to avoid truncating evidence.
Database triggers make file records and contact/file links immutable. Composite
foreign keys prevent moving an evidence link to a different shop or tenant and
prevent deleting referenced contacts/files. This retains evidence even after a
contact has posted financial records. No file deletion API is exposed.

## Opening obligations and journal integration

Example opening request (the rate/book value is reviewed input, not a quote):

```json
{
  "role": "customer",
  "direction": "receivable",
  "currency_id": 1,
  "amount": "10000.00",
  "book_afn": "10000.00",
  "reason": "Owner reviewed opening books",
  "owner_approved": true
}
```

For `receivable`, debit the contact's receivable account and credit opening
equity. For `payable`, credit the contact's payable account and debit opening
equity. Both original-currency amounts and AFN book values balance. Cash,
revenue, and expense accounts are untouched. AFN amount and AFN book value must
match; foreign obligations require a positive reviewed AFN book value. Precision
comes from the configured currency. Amounts are exact decimal strings; Persian
and Arabic digits are normalized, floats and scientific notation are rejected.

Only the current organization owner can approve an opening, with
`owner_approved=true`. Each contact/role/direction/currency can have one opening,
before any activity in that account. Repeated identical retry keys return the
original journal result. Reusing a key with a changed payload returns 409. The
shared tenant lock, immutable account mappings, unique opening keys and retry
records protect simultaneous requests. All effects share one transaction.

Later posting services use `ContactLedgerService.account` to resolve the shared
receivable/payable ledger account, then post their complete workflow through the
Phase 6 posting kernel. Do not append independent contact balance rows or infer
ownership from a client-supplied account ID. Original and linked reversal lines
are naturally included in statements; later workflow services own their reversal
and allocation rules. Contact openings are retained and cannot be edited or
silently reposted with a different key.

## Balance and statement conventions

Receivable means **contact owes shop**. Payable means **shop owes contact**. Both
are returned separately as positive gross obligation amounts; `net_receivable`
is receivable minus payable, without netting roles or currencies. A dual-role
contact therefore has independent customer and agent totals.

Statement movement amounts use the journal sign: debit positive, credit negative.
Positive values increase net receivable, negative values decrease it. For each
role/currency, `running_net_receivable` starts at the period opening and follows
entry timestamp, entry ID, then line ID. Amounts never pass through binary float
or SQLite numeric aggregation. Every movement exposes `entry_id`, `line_id`,
original currency, AFN book value, action, reason and timestamp. Only sealed
(posted) journals contribute.

`date_from` and `date_to` are inclusive Kabul business dates. The implementation
converts them to a half-open UTC interval, including local midnight at the start
and excluding midnight after the end. Earlier posted entries form opening
balances. `offset` and `limit` (1–1,000) paginate movements only; opening and
closing totals cover the entire selected period. No period end means all posted
entries are included. Inactive currencies with historical activity remain
visible. Currencies without activity are omitted, not converted or fabricated.

CSV includes shop/contact names, contact UUID, filters, Kabul timezone, generation
time, opening totals, all filtered movements and closing totals. Untrusted text
cells are escaped against spreadsheet formula execution. CSV never includes
identification fields or attachment contents.

## Validation

Coverage includes contact roles and deactivation, Unicode/phone search and
duplicates, role and organization isolation, private identification/file access,
immutable evidence, composite ownership FKs, owner-reviewed debt openings,
failed-post rollback, duplicate and concurrent retries, exact gross balances,
linked journal corrections, Kabul cutoff boundaries, movement pagination and
CSV exports. The migration tests apply and reverse the actual revisions with
foreign keys enabled and compare the resulting schema to ORM metadata.

## Agent settlements (Phase 10)

See [hawalas.md](hawalas.md) for allocatable agent openings, transfer principal/fee
obligations, manual cash/external settlements and linked reversals. The existing
contact balances remain ledger-derived, with gross agent receivables/payables per
currency. Agent-only statements and CSV exports are available through
`GET /agent-settlements/statement?agent_uuid=...`; customer-role rows remain separate.
