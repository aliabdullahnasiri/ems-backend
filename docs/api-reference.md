# API endpoint inventory

Generated from the mounted FastAPI OpenAPI schema. Paths include `/api/v1`.

Use the running `/openapi.json` for complete parameter constraints, request and response schemas. The interactive reference is `/docs`; `/redoc` is also available. Domain guides explain accounting and authorization requirements beyond schema validation.

| Method | Path | Operation | Request schema | Success |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | Register | RegisterRequest | 201 |
| POST | `/api/v1/auth/login` | Login | LoginRequest | 200 |
| GET | `/api/v1/auth/me` | Me | — | 200 |
| POST | `/api/v1/auth/logout` | Logout | — | 200 |
| POST | `/api/v1/auth/refresh` | Refresh | — | 200 |
| GET | `/api/v1/auth/sessions` | Get Sessions | — | 200 |
| DELETE | `/api/v1/auth/sessions/{session_uuid}` | Revoke Session | — | 200 |
| POST | `/api/v1/auth/sessions/revoke-all` | Revoke All Sessions | — | 200 |
| POST | `/api/v1/auth/password/forgot` | Password Forgot | ForgotPasswordRequest | 200 |
| POST | `/api/v1/auth/password/reset` | Password Reset | ResetPasswordRequest | 200 |
| POST | `/api/v1/auth/password/change` | Password Change | ChangePasswordRequest | 200 |
| POST | `/api/v1/auth/email/verify` | Verify Email | VerifyEmailRequest | 200 |
| POST | `/api/v1/auth/email/resend` | Resend Verification Email | ResendVerificationEmailRequest | 200 |
| POST | `/api/v1/auth/2fa/otp/setup` | Setup Otp | — | 200 |
| POST | `/api/v1/auth/2fa/otp/resend` | Resend Otp | — | 200 |
| POST | `/api/v1/auth/2fa/otp/enable` | Enable Otp | OTPEnableRequest | 200 |
| POST | `/api/v1/auth/2fa/otp/disable` | Disable Otp | TwoFactorDisableRequest | 200 |
| POST | `/api/v1/auth/2fa/totp/setup` | Setup 2Fa | — | 200 |
| POST | `/api/v1/auth/2fa/totp/enable` | Enable 2Fa | TOTPEnableRequest | 200 |
| POST | `/api/v1/auth/2fa/totp/disable` | Disable 2Fa | TwoFactorDisableRequest | 200 |
| POST | `/api/v1/auth/2fa/totp/recovery-codes` | Regenerate Recovery Codes | TOTPVerifyRequest | 200 |
| POST | `/api/v1/auth/2fa/verify` | Verify 2Fa | TOTPVerifyRequest | 200 |
| POST | `/api/v1/auth/organizations/{organization_uuid}/select` | Select Organization | — | 200 |
| GET | `/api/v1/auth/organization` | Current Organization | — | 200 |
| GET | `/api/v1/auth/context` | Current Context | — | 200 |
| GET | `/api/v1/health/` | Health Check | — | 200 |
| GET | `/api/v1/users/me` | Get Me | — | 200 |
| DELETE | `/api/v1/users/me` | Delete Me | — | 204 |
| PATCH | `/api/v1/users/me` | Update Me | UpdateUserRequest | 200 |
| PATCH | `/api/v1/users/me/email` | Update My Email | UpdateEmailRequest | 200 |
| PATCH | `/api/v1/users/me/username` | Update My Username | UpdateUsernameRequest | 200 |
| POST | `/api/v1/organizations/{organization_uuid}/join-requests` | Request To Join Organization | — | 202 |
| POST | `/api/v1/organizations` | Create Organization | CreateOrganizationRequest | 201 |
| GET | `/api/v1/organizations` | List Organizations | — | 200 |
| GET | `/api/v1/organizations/{organization_uuid}` | Get Organization | — | 200 |
| PATCH | `/api/v1/organizations/{organization_uuid}` | Update Organization | UpdateOrganizationRequest | 200 |
| DELETE | `/api/v1/organizations/{organization_uuid}` | Delete Organization | — | 204 |
| GET | `/api/v1/organizations/{organization_uuid}/members` | List Members | — | 200 |
| POST | `/api/v1/organizations/{organization_uuid}/members` | Add Member | AddMemberRequest | 201 |
| PATCH | `/api/v1/organizations/{organization_uuid}/members/{member_uuid}` | Update Member | UpdateMemberRequest | 200 |
| DELETE | `/api/v1/organizations/{organization_uuid}/members/{member_uuid}` | Remove Member | — | 204 |
| GET | `/api/v1/organizations/{organization_uuid}/roles` | List Roles | — | 200 |
| POST | `/api/v1/organizations/{organization_uuid}/roles` | Create Role | CreateRoleRequest | 201 |
| GET | `/api/v1/organizations/{organization_uuid}/roles/{role_uuid}` | Get Role | — | 200 |
| PATCH | `/api/v1/organizations/{organization_uuid}/roles/{role_uuid}` | Update Role | UpdateRoleRequest | 200 |
| DELETE | `/api/v1/organizations/{organization_uuid}/roles/{role_uuid}` | Delete Role | — | 204 |
| GET | `/api/v1/organizations/{organization_uuid}/permissions` | List Permissions | — | 200 |
| GET | `/api/v1/organizations/{organization_uuid}/roles/{role_uuid}/permissions` | Get Role Permissions | — | 200 |
| PUT | `/api/v1/organizations/{organization_uuid}/roles/{role_uuid}/permissions` | Set Role Permissions | SetRolePermissionsRequest | 200 |
| GET | `/api/v1/organizations/{organization_uuid}/members/{member_uuid}/roles` | Get Member Roles | — | 200 |
| PUT | `/api/v1/organizations/{organization_uuid}/members/{member_uuid}/roles` | Set Member Roles | SetMemberRolesRequest | 200 |
| POST | `/api/v1/shops` | Create Shop | ShopCreate | 201 |
| GET | `/api/v1/shops` | List Shops | — | 200 |
| GET | `/api/v1/shops/export` | Export Shops | — | 200 |
| GET | `/api/v1/shops/summary` | Shop Summary | — | 200 |
| GET | `/api/v1/shops/{shop_uuid}` | Get Shop | — | 200 |
| PATCH | `/api/v1/shops/{shop_uuid}` | Update Shop | ShopUpdate | 200 |
| POST | `/api/v1/files` | Upload File | Body_upload_file_api_v1_files_post | 201 |
| GET | `/api/v1/files/{attachment_uuid}` | Download File | — | 200 |
| GET | `/api/v1/audit-logs` | Audit Logs | — | 200 |
| POST | `/api/v1/contacts` | Create Contact | ContactCreateRequest | 201 |
| GET | `/api/v1/contacts` | List Contacts | — | 200 |
| GET | `/api/v1/contacts/{contact_uuid}` | Get Contact | — | 200 |
| PATCH | `/api/v1/contacts/{contact_uuid}` | Update Contact | ContactUpdateRequest | 200 |
| POST | `/api/v1/contacts/{contact_uuid}/opening-balances` | Opening Balance | ContactOpeningRequest | 201 |
| GET | `/api/v1/contacts/{contact_uuid}/balances` | Contact Balances | — | 200 |
| GET | `/api/v1/contacts/{contact_uuid}/statement` | Contact Statement | — | 200 |
| GET | `/api/v1/expense-categories` | Categories | — | 200 |
| POST | `/api/v1/expense-categories` | Create Category | CategoryRequest | 201 |
| PATCH | `/api/v1/expense-categories/{record_uuid}` | Update Category | CategoryUpdateRequest | 200 |
| POST | `/api/v1/expenses` | Create Expense | ExpenseRequest | 201 |
| GET | `/api/v1/expenses` | List Expense | — | 200 |
| POST | `/api/v1/expenses/{record_uuid}/reverse` | Reverse Expense | ReverseRequest | 201 |
| POST | `/api/v1/cash-transfers` | Create Transfer | TransferRequest | 201 |
| GET | `/api/v1/cash-transfers` | List Transfer | — | 200 |
| POST | `/api/v1/cash-transfers/{record_uuid}/reverse` | Reverse Transfer | ReverseRequest | 201 |
| POST | `/api/v1/business-days/open` | Open Day | OpenDayRequest | 201 |
| GET | `/api/v1/business-days/current` | Current Day | — | 200 |
| POST | `/api/v1/day-closings` | Create Closing | ClosingRequest | 201 |
| GET | `/api/v1/day-closings` | List Closings | — | 200 |
| GET | `/api/v1/day-closings/{record_uuid}` | Get Closing | — | 200 |
| GET | `/api/v1/day-closings/{record_uuid}/export` | Export Closing | — | 200 |
| POST | `/api/v1/day-closings/{record_uuid}/confirm` | Confirm Closing | ConfirmClosingRequest | 201 |
| GET | `/api/v1/reports/daily` | Report Daily | — | 200 |
| GET | `/api/v1/reports/cash` | Report Cash | — | 200 |
| GET | `/api/v1/reports/outstanding` | Report Outstanding | — | 200 |
| GET | `/api/v1/reports/profit` | Report Profit | — | 200 |
| GET | `/api/v1/reports/hawala` | Report Hawala | — | 200 |
| GET | `/api/v1/reports/audit` | Report Audit | — | 200 |
| GET | `/api/v1/dashboard` | Dashboard | — | 200 |
| GET | `/api/v1/settings` | Settings | — | 200 |
| PATCH | `/api/v1/settings` | Update Settings | SettingsUpdateRequest | 200 |
| GET | `/api/v1/currencies` | Currencies | — | 200 |
| POST | `/api/v1/currencies` | Create Currency | CurrencyCreateRequest | 201 |
| PATCH | `/api/v1/currencies/{currency_uuid}` | Update Currency | CurrencyUpdateRequest | 200 |
| POST | `/api/v1/cashboxes` | Create Cashbox | CashboxCreateRequest | 201 |
| GET | `/api/v1/cashboxes` | Cashboxes | — | 200 |
| PATCH | `/api/v1/cashboxes/{cashbox_uuid}` | Update Cashbox | CashboxUpdateRequest | 200 |
| POST | `/api/v1/opening-balances` | Opening | OpeningBalanceRequest | 201 |
| POST | `/api/v1/opening-balances/{entry_uuid}/reverse` | Reverse Opening | ReversalRequest | 201 |
| GET | `/api/v1/ledger/entries` | Entries | — | 200 |
| GET | `/api/v1/cashboxes/{cashbox_uuid}/movements` | Movements | — | 200 |
| POST | `/api/v1/rates` | Create Rate | RateCreateRequest | 201 |
| GET | `/api/v1/rates` | Rates | — | 200 |
| POST | `/api/v1/exchanges/quote` | Quote | ExchangeQuoteRequest | 200 |
| POST | `/api/v1/exchanges` | Create Exchange | ExchangeCreateRequest | 201 |
| GET | `/api/v1/exchanges` | Exchanges | — | 200 |
| GET | `/api/v1/exchanges/{exchange_uuid}` | Exchange | — | 200 |
| GET | `/api/v1/exchanges/{exchange_uuid}/receipt` | Receipt | — | 200 |
| POST | `/api/v1/exchanges/{exchange_uuid}/reverse` | Reverse | ExchangeReverseRequest | 201 |
| POST | `/api/v1/payments` | Create Payment | PaymentCreateRequest | 201 |
| GET | `/api/v1/payments` | Payments | — | 200 |
| GET | `/api/v1/payments/{payment_uuid}` | Get Payment | — | 200 |
| GET | `/api/v1/payments/{payment_uuid}/receipt` | Receipt | — | 200 |
| POST | `/api/v1/payments/{payment_uuid}/reverse` | Reverse | PaymentReverseRequest | 201 |
| GET | `/api/v1/contacts/{contact_uuid}/outstanding` | Outstanding | — | 200 |
| POST | `/api/v1/hawalas` | Create | HawalaCreateRequest | 201 |
| GET | `/api/v1/hawalas` | Hawalas | — | 200 |
| GET | `/api/v1/hawalas/{hawala_uuid}` | Hawala | — | 200 |
| PATCH | `/api/v1/hawalas/{hawala_uuid}` | Update | HawalaUpdateRequest | 200 |
| GET | `/api/v1/hawalas/{hawala_uuid}/receipt` | Receipt | — | 200 |
| POST | `/api/v1/hawalas/{hawala_uuid}/confirm` | Confirm | HawalaConfirmRequest | 200 |
| POST | `/api/v1/hawalas/{hawala_uuid}/pay` | Pay | HawalaPayRequest | 200 |
| POST | `/api/v1/hawalas/{hawala_uuid}/cancel` | Cancel | HawalaCancelRequest | 200 |
| POST | `/api/v1/hawalas/{hawala_uuid}/refund` | Refund | HawalaRefundRequest | 200 |
| POST | `/api/v1/hawalas/{hawala_uuid}/reviews` | Review | HawalaReviewRequest | 200 |
| POST | `/api/v1/agent-settlements` | Settle | AgentSettlementCreateRequest | 201 |
| GET | `/api/v1/agent-settlements` | Settlements | — | 200 |
| GET | `/api/v1/agent-settlements/statement` | Statement | — | 200 |
| GET | `/api/v1/agent-settlements/{settlement_uuid}` | Settlement | — | 200 |
| POST | `/api/v1/agent-settlements/{settlement_uuid}/reverse` | Reverse | PaymentReverseRequest | 201 |
| GET | `/api/v1/contacts/{agent_uuid}/agent-outstanding` | Outstanding | — | 200 |

Total: 132 operations. Regenerate this inventory when mounted routes change.
