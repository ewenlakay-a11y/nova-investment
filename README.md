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
- Four supplied investment scenarios
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
