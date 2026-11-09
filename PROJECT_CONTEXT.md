# EMS — Exchange Management System

Version 1 is for one sarafi shop, with an owner and cashiers.

## Simple PRD

### 1. Purpose

Help Afghan money exchangers manage currency exchange, customer debts, hawala, and daily cash in one place.

Cash and hawala deserve early priority: the World Bank’s March 2024 business survey reported substantial reliance on both in Afghanistan. [World Bank survey](https://www.worldbank.org/en/news/press-release/2024/03/19/afghanistans-private-sector-still-facing-significant-challenges)

### 2. Users

- **Owner:** manages staff, rates, reports, corrections, and closing.
- **Cashier:** records exchanges, payments, and hawala.
- **Accountant:** reviews balances, statements, and reports.

### 3. Problems EMS should solve

These are proposed needs to validate with 2–3 local sarafis.

| Problem | EMS solution |
|---|---|
| Paper records make transactions difficult to find | Searchable history and numbered receipts |
| Different currencies become mixed in calculations | Separate cash and account balances for each currency |
| Customer debts are unclear | Clearly show “Customer owes us” and “We owe customer” |
| Hawala payout and agent settlement are difficult to track | Transfer references, payout status, and agent statements |
| Cash does not match the recorded balance | Daily counting, difference tracking, and owner approval |
| Internet interruptions stop work | Run on one shared computer/server over the shop’s local network |
| English-only software is difficult for staff | Dari/Pashto interface, right-to-left layout, and local-language receipts |

### 4. First-release features

- Currency buying and selling.
- AFN, USD, PKR, and configurable additional currencies.
- Manual buying/selling rates with clear rate direction.
- Cashboxes and opening balances.
- Customers, agents, debts, and partial payments.
- Incoming/outgoing hawala with fees and settlement tracking.
- Receipts, expenses, daily closing, and basic reports.
- Staff permissions, audit history, and backups.

**Daily flow:**

Open day → Record transactions → Give receipts → Review outstanding balances → Count cash → Close day.

### 5. Important rules

- Every amount must have a currency; never directly add different currencies.
- Keep the agreed rate on each transaction.
- Update cash, customer balances, and accounting entries together.
- Prevent duplicate transactions and duplicate hawala payouts.
- Correct posted transactions through linked reversals.
- Keep customer money and hawala principal separate from earned income.
- Record who performed each important action and when.
- Collect identity information and prepare reporting exports according to the operator’s applicable requirements, confirmed before launch using current [DAB requirements](https://dab.gov.af/Regulations).

### 6. Success criteria

During the pilot:

- A trained cashier completes a normal exchange within 30 seconds.
- Staff can explain every customer and agent balance from its transaction history.
- Daily closing shows expected cash, counted cash, and any difference.
- Daily operations continue during an internet outage while the local server and network remain available.
- A backup can be restored successfully.

### 7. Later versions

Multiple branches, remote access, disconnected-device synchronization, automatic rate feeds, bank integrations, WhatsApp messages, and SaaS subscriptions.
