---
doc_id: account-access
doc_version: 2.1
effective_date: 2026-04-01
category: account_access
---
## HB-ACC-01 | Password reset
keywords: password, reset, forgot, log in, login, sign in, cannot access my account, can't access my account
action: send_password_reset_link
Agents may send a password reset link to the email address on the customer account. Never send a link to a different address than the one on file, and never ask the customer for their current password.

## HB-ACC-02 | Locked account after failed attempts
keywords: locked, locked out, too many attempts, account blocked, account is blocked
action: unlock_account
An account locks for 30 minutes after five failed sign-in attempts. Agents may unlock it early once the customer has confirmed the email address and postcode on the account.

## HB-ACC-03 | Suspected account takeover
keywords: hacked, someone else, not me, unknown login, suspicious, compromised
action: escalate_to_human
If a customer reports orders or sign-ins they did not make, treat it as a possible account takeover: escalate to the security lead and do not change account details from the ticket.
