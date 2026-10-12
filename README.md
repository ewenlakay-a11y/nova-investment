# Nova Investment — Flask prototype

This repository is the existing Nova Investment application. It is a Flask + SQLite prototype and is not a bank, brokerage, custodian, or payment processor.

## Run locally
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Run `pip install -r requirements.txt`.
4. Set a private `SECRET_KEY` environment variable (use a long random value).
5. To create/refresh an administrator, set `ADMIN_EMAIL` and `ADMIN_PASSWORD` environment variables. The admin password must be at least 12 characters.
6. Run `python app.py`.
7. Open `http://127.0.0.1:5000`.

Do not put real passwords or secret keys in this repository.

## Deploying on Render
Keep using the existing `nova-investment-project` service and its connected repository. In Render, open the service's **Environment** settings and configure:
- `SECRET_KEY`: a long, unique random secret (Render's existing generated value is suitable; do not publish it).
- `ADMIN_EMAIL`: the dedicated administrator email address.
- `ADMIN_PASSWORD`: a unique password of at least 12 characters.

Save the settings and deploy. The application no longer creates public demo accounts. The previously documented `admin@novainvestment.local` and `demo@novainvestment.local` accounts are disabled/demoted at startup; use the environment variables above to establish the administrator account. Keep these values private and never commit them to GitHub.

**Database persistence:** the current `render.yaml` uses `/tmp/nova_investment.db`, which is ephemeral on many hosts. Before relying on real user data, configure a persistent Render disk/database and point `DATABASE_PATH` to its mounted path. Back up any existing data before changing the path.

## Included features
- Registration and login (passwords hashed with Werkzeug)
- Single account per email address
- Portfolio dashboard and illustrative monthly/12-month ROI calculations
- Investment requests and payment-submission workflow
- Admin review of submitted investment payments
- Deposit, withdrawal, redemption, and early-withdrawal request workflow
- Twelve-month maturity rules and early-withdrawal request with 2% illustrative fee
- Transaction history and notifications
- In-app customer support messages and administrator replies
- Admin/owner dashboard
- Statements, security page, responsive layout, BTC market widget

## Security notes
- Passwords are stored using Werkzeug password hashes; existing plaintext passwords are upgraded during startup.
- CSRF protection is enabled for POST forms.
- Session cookies are HTTP-only, SameSite=Lax, and Secure when `RENDER` is set.
- Security headers are added.
- Default/demo accounts are not left usable. Configure the administrator via Render environment variables.
- This is a prototype, not a production-ready financial service. Before handling real money, obtain qualified legal/compliance/security review and use legitimate payment, identity-verification, custody, and financial-data providers.

## ROI calculation
Plan ROI is an illustrative monthly percentage applied to original principal using simple interest, with no compounding. Twelve-month projected profit = principal × monthly rate × 12; projected total = principal + projected profit. These are projections, not guaranteed returns. Unmatured projected profit is not available for early withdrawal.


## Latest requested changes (October 2026)
- All displayed plans use a 50% monthly ROI projection and 6-month maturity. The first plan is $3,000 with a $500 initial installment.
- Every plan discloses a 10% penalty on an overdue installment after a 5-day grace period. Confirm enforceability and terms with qualified counsel before applying fees.
- Email one-time verification codes are required for both signup and every login, on mobile and desktop. Codes expire after 10 minutes and have a 5-attempt limit.
- Configure `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in Render before using signup/login. Without SMTP configuration, verification deliberately fails closed.
- Returns shown are projections, not guaranteed earnings. A 50% monthly return is exceptionally high and should not be advertised as guaranteed.
