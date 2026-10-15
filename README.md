# Nova Investment — Final Year Project

A browser-based Flask + SQLite investment-management prototype for academic assessment.

## Run locally
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Run `pip install -r requirements.txt`
4. Run `python app.py`
5. Open http://127.0.0.1:5000

## Assessment accounts
Investor:
- Email: demo@novainvestment.local
- Password: demo1234

Admin:
- Email: admin@novainvestment.local
- Password: admin1234

Demo 2FA code: 246810

## Included
- Registration/login
- Persistent SQLite database
- Portfolio dashboard
- Six investment plans ($3,000 to $18,000) with six-month terms and 50% monthly illustrative ROI
- Illustrative ROI calculation
- Deposit / withdrawal / redeem request workflow
- Bitcoin payment address
- Transaction history
- Maturity dates
- Notifications
- Animated BTC market notification
- Support inbox and admin replies
- Admin/owner dashboard
- Security/2FA demonstration
- Account statements
- Responsive mobile layout

## Important project note
The supplied investment figures are displayed as illustrative project scenarios, not guaranteed returns. The application does not connect to banks, exchanges, custody systems, or move real money. The Bitcoin address is displayed as the payment-method requirement supplied for the project; real-money deployment would require appropriate legal, regulatory, KYC/AML, custody and payment infrastructure.

The BTC notification attempts to read a public CoinGecko BTC/USD endpoint in the visitor's browser. If unavailable, it reports that market data is unavailable rather than inventing a price or fabricating current investors.


## Email security setup
New accounts must verify their email, sign-ins use a 6-digit email code, password recovery uses a 6-digit email code, and withdrawal requests require a receiving wallet address plus an email verification code. Passwords are hashed, OTPs expire after 10 minutes, and admin routes require an admin session.

For Gmail delivery on Render, configure these environment variables on the web service:
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USER=novainvestment965@gmail.com`
- `SMTP_PASSWORD=<Google App Password>`
- `SMTP_FROM=novainvestment965@gmail.com`

Use a Google App Password rather than the normal Gmail password. The app does not read customers' Gmail accounts; it only sends security codes to the email address they registered.


## Updated six-month plan assumptions
- Plans: $3,000, $6,000, $9,000, $12,000, $15,000, and $18,000.
- Each plan displays a 50% monthly illustrative ROI for six months, based on original principal and simple interest.
- Example: $3,000 principal → $1,500 monthly projected profit → $9,000 projected six-month profit → $12,000 projected total.
- A suggested installment amount (plan principal divided by six) is displayed. A 5-day grace period and 10% overdue-installment penalty policy are displayed.
- Important implementation limitation: this package's payment workflow still submits the full plan amount in one payment; installment collection and automatic late-fee assessment are not implemented. The penalty is a policy notice only and should not be represented as automatically enforced.
- Projected ROI is not credited to available cash. Account balance calculations use recorded principal and approved transactions; projected returns are shown separately.
- This is a prototype, not a verified investment product. The unusually high ROI figures are illustrative only and must not be presented as guaranteed returns. Confirm legal and payment-provider eligibility before accepting real money.

## Render deployment dependencies
The requirements file includes Flask-WTF as well as Flask and Gunicorn. Render's build command should be `pip install -r requirements.txt`, with start command `gunicorn app:app`. Configure the SMTP environment variables listed above before relying on email verification.
