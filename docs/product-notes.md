# Product notes

Captured from the product owner. Company name is **Monolith**. Do not invent a domain or Stripe plan. Open items are marked TBD.

## Charging (INTERNAL — never on landing page or customer UI)

These figures are internal planning notes only. They must never appear on the landing page or any customer UI.

- Free → watch 3
- Individuals → $20
- Teams/Org → $100
- Stripe not set up yet. Moving to a free version: **all accounts default to free**.
- Timestamp every org/individual onboarded.
- Start with **one month free**.
- Do **not** put pricing on the landing page.
- Do **not** show org/team prices on the UI.

Stripe: not set up yet. Plan details TBD.

## Still needed

- Company name — **Monolith**
- Domain — TBD (will be something related to monolith; do not invent the hostname)
- Email for the org — TBD

## Accounts

- Admin onboards Teams/Org.
- Individuals sign up and sign in themselves.
- Only **one** individual account session with those credentials (one at a time).
- **Infinite** team accounts can be signed in (company Gmail, SSO, etc.).

## Implementation order (easy → hard)

1. Weather cache + clean errors — **done**
2. One poller (job refreshes; browsers read cache) — **this PR**
3. Side nav — **done**
4. Landing page (no pricing) — **this PR**
5. Individual sign up / sign in, default free, onboard timestamp, 1 month trial, 3 watches — **this PR**
6. One session per individual — **this PR**
7. Admin onboards Teams/Org — later
8. Team multi-login / SSO — later
9. Stripe — later
10. Custom domain — later (user buying)
