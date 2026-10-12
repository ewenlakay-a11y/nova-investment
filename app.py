
import os, sqlite3, secrets, smtplib, hashlib, hmac
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY")
if not secret_key and os.environ.get("RENDER"):
    raise RuntimeError("SECRET_KEY must be set in the deployment environment.")
app.secret_key = secret_key or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    SESSION_COOKIE_SAMESITE="Lax",
    WTF_CSRF_TIME_LIMIT=3600,
)
csrf = CSRFProtect(app)

@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response

DB = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "nova_investment.db"))

BTC_ADDRESS = "bc1qwe7l499zq0y2l7x59lvnwlft02v0dhzh34q5jy"
SUPPORT_EMAIL = "novainvestment965@gmail.com"

PLANS = [
    {"id":"growth","name":"Growth","amount":3000,"initial_payment":500,"roi":50,"months":6},
    {"id":"plus","name":"Plus","amount":5000,"initial_payment":round(5000/6,2),"roi":50,"months":6},
    {"id":"pro","name":"Pro","amount":10000,"initial_payment":round(10000/6,2),"roi":50,"months":6},
    {"id":"elite","name":"Elite","amount":20000,"initial_payment":round(20000/6,2),"roi":50,"months":6},
]

# User-specified projections only; returns are not guaranteed.
LATE_FEE_RATE = 0.10
GRACE_DAYS = 5
OTP_MINUTES = 10
for _p in PLANS:
    _p["monthly_profit"] = _p["amount"] * (_p["roi"] / 100)
    _p["roi_amount"] = _p["monthly_profit"] * _p["months"]
    _p["annual_contribution"] = _p["amount"]
    _p["annual_profit"] = _p["roi_amount"]
    _p["annual_total"] = _p["amount"] + _p["annual_profit"]
    _p["target"] = _p["annual_total"]
    _p["penalty_rate"] = LATE_FEE_RATE
    _p["grace_days"] = GRACE_DAYS


def send_verification_email(email, code, purpose):
    host=os.environ.get("SMTP_HOST"); username=os.environ.get("SMTP_USER"); password=os.environ.get("SMTP_PASSWORD")
    sender=os.environ.get("SMTP_FROM", username or ""); port=int(os.environ.get("SMTP_PORT", "587"))
    if not all([host, username, password, sender]):
        app.logger.error("Email verification unavailable: configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM")
        return False
    msg=EmailMessage(); msg["Subject"]=f"Nova Investment {purpose} verification code"; msg["From"]=sender; msg["To"]=email
    msg.set_content(f"Your Nova Investment verification code is {code}. It expires in {OTP_MINUTES} minutes. If you did not request it, ignore this message.")
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(); server.login(username, password); server.send_message(msg)
        return True
    except Exception:
        app.logger.exception("Failed to send verification email")
        return False

def create_email_code(email, purpose, payload=""):
    code=f"{secrets.randbelow(1000000):06d}"
    now=datetime.now(); expires=(now+timedelta(minutes=OTP_MINUTES)).isoformat()
    c=db(); c.execute("DELETE FROM email_codes WHERE email=? AND purpose=?",(email,purpose))
    c.execute("INSERT INTO email_codes(email,purpose,code_hash,payload,expires_at,attempts,created_at) VALUES(?,?,?,?,?,?,?)",
              (email,purpose,hashlib.sha256(code.encode()).hexdigest(),payload,expires,0,now.isoformat()))
    c.commit(); c.close()
    return code

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
      is_admin INTEGER DEFAULT 0, twofa INTEGER DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS investments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      plan TEXT NOT NULL, amount REAL NOT NULL, target REAL NOT NULL,
      status TEXT NOT NULL, maturity TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      kind TEXT NOT NULL, amount REAL NOT NULL, status TEXT NOT NULL,
      note TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS support(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      message TEXT NOT NULL, reply TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
      title TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
    );
    """)
    c.execute("CREATE TABLE IF NOT EXISTS email_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, purpose TEXT NOT NULL, code_hash TEXT NOT NULL, payload TEXT, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    cols=[r[1] for r in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if "investment_id" not in cols:
        c.execute("ALTER TABLE transactions ADD COLUMN investment_id INTEGER")
    invcols=[r[1] for r in c.execute("PRAGMA table_info(investments)").fetchall()]
    if "payment_submitted_at" not in invcols:
        c.execute("ALTER TABLE investments ADD COLUMN payment_submitted_at TEXT")
    for col,typ in [("monthly_profit","REAL"),("annual_profit","REAL"),("annual_total","REAL"),("roi","REAL"),("roi_amount","REAL")]:
        if col not in invcols:
            c.execute(f"ALTER TABLE investments ADD COLUMN {col} {typ}")
    # Refresh the projection fields for existing investments as well as new ones.
    # This changes displayed projections; it does not create a payment or transaction.
    c.execute("""UPDATE investments SET
        monthly_profit=amount*(COALESCE(roi,0)/100.0),
        annual_profit=amount*(COALESCE(roi,0)/100.0)*12,
        annual_total=amount+(amount*(COALESCE(roi,0)/100.0)*12),
        roi_amount=amount*(COALESCE(roi,0)/100.0)*12,
        target=amount+(amount*(COALESCE(roi,0)/100.0)*12)
        WHERE roi IS NOT NULL AND roi > 0""")
    # Hash legacy plaintext passwords once, and disable known demo/default accounts.
    # New accounts are always hashed by the registration route.
    for row in c.execute("SELECT id,email,password FROM users").fetchall():
        stored = row["password"] or ""
        if not (stored.startswith("scrypt:") or stored.startswith("pbkdf2:")):
            c.execute("UPDATE users SET password=? WHERE id=?",
                      (generate_password_hash(stored), row["id"]))
    for default_email in ("admin@novainvestment.local", "demo@novainvestment.local"):
        row = c.execute("SELECT id FROM users WHERE lower(email)=?", (default_email,)).fetchone()
        if row:
            # Never leave a publicly documented default account active.
            c.execute("UPDATE users SET is_admin=0, password=? WHERE id=?",
                      (generate_password_hash(secrets.token_urlsafe(48)), row["id"]))
    # Optional first administrator bootstrap. Configure ADMIN_EMAIL and ADMIN_PASSWORD
    # as private Render environment variables; never put credentials in source code.
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_email and admin_password:
        if len(admin_password) < 12:
            raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters.")
        existing = c.execute("SELECT id FROM users WHERE lower(email)=?", (admin_email,)).fetchone()
        if existing:
            c.execute("UPDATE users SET is_admin=1, password=? WHERE id=?",
                      (generate_password_hash(admin_password), existing["id"]))
        else:
            c.execute("INSERT INTO users(name,email,password,is_admin,created_at) VALUES(?,?,?,?,?)",
                      ("Nova Administrator", admin_email, generate_password_hash(admin_password), 1, datetime.now().isoformat()))
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a,**kw):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*a,**kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get("is_admin"): return redirect(url_for("dashboard"))
        return f(*a,**kw)
    return w

def user():
    if "user_id" not in session: return None
    c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone(); c.close()
    return u

@app.context_processor
def ctx():
    return {"current_user":user(),"btc_address":BTC_ADDRESS,"support_email":SUPPORT_EMAIL,"plans":PLANS,"grace_days":GRACE_DAYS,"late_fee_percent":int(LATE_FEE_RATE*100)}

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/activity-data")
def activity_data():
    c=db()
    rows=c.execute("""SELECT users.name, investments.amount, investments.created_at FROM investments JOIN users ON users.id=investments.user_id WHERE investments.status='active' ORDER BY investments.id DESC LIMIT 12""").fetchall()
    c.close()
    activities=[]
    for r in rows:
        name=(r['name'] or 'Customer').strip().split()[0]
        masked=(name[:1]+"***") if name else "Customer"
        activities.append({"label":f"{masked} has an approved ${r['amount']:,.0f} investment"})
    return jsonify({"activities":activities})

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        if not name or "@" not in email or len(password)<12:
            flash("Please complete all fields with a valid email. Password must contain at least 12 characters.","error"); return redirect(url_for("register"))
        c=db(); exists=c.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)",(email,)).fetchone(); c.close()
        if exists:
            flash("That email already has an account. Please log in.","error"); return redirect(url_for("login"))
        session["pending_signup"]={"name":name,"email":email,"password":generate_password_hash(password)}
        code=create_email_code(email,"signup")
        if not send_verification_email(email,code,"signup"):
            session.pop("pending_signup",None)
            flash("Email verification is not available yet. The site administrator must configure SMTP before signup can continue.","error")
            return redirect(url_for("register"))
        session["verify_email"]=email; session["verify_purpose"]="signup"
        return redirect(url_for("verify_email_code"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        c=db(); u=c.execute("SELECT * FROM users WHERE lower(email)=?",(email,)).fetchone()
        valid=False
        if u:
            stored=u["password"] or ""
            if stored.startswith(("scrypt:","pbkdf2:")):
                try: valid=check_password_hash(stored,password)
                except (ValueError,TypeError): valid=False
            else:
                valid=secrets.compare_digest(stored,password)
                if valid:
                    c.execute("UPDATE users SET password=? WHERE id=?",(generate_password_hash(password),u["id"])); c.commit()
        c.close()
        if not u or not valid:
            flash("Invalid login details.","error"); return redirect(url_for("login"))
        code=create_email_code(email,"login")
        if not send_verification_email(email,code,"login"):
            flash("Email verification is not available. Please contact the administrator.","error"); return redirect(url_for("login"))
        session.clear(); session["verify_email"]=email; session["verify_purpose"]="login"
        return redirect(url_for("verify_email_code"))
    return render_template("login.html")

@app.route("/verify-email-code", methods=["GET","POST"])
def verify_email_code():
    email=session.get("verify_email"); purpose=session.get("verify_purpose")
    if not email or purpose not in ("signup","login"):
        return redirect(url_for("login"))
    if request.method=="POST":
        supplied=request.form.get("code","").strip()
        c=db(); row=c.execute("SELECT * FROM email_codes WHERE email=? AND purpose=? ORDER BY id DESC LIMIT 1",(email,purpose)).fetchone()
        if not row or row["attempts"]>=5 or datetime.fromisoformat(row["expires_at"]) < datetime.now():
            c.close(); session.pop("verify_email",None); session.pop("verify_purpose",None)
            flash("Verification code expired or too many attempts. Please start again.","error")
            return redirect(url_for("register" if purpose=="signup" else "login"))
        c.execute("UPDATE email_codes SET attempts=attempts+1 WHERE id=?",(row["id"],))
        good=hmac.compare_digest(row["code_hash"],hashlib.sha256(supplied.encode()).hexdigest())
        if not good:
            c.commit(); c.close(); flash("Incorrect verification code. Please try again.","error"); return redirect(url_for("verify_email_code"))
        c.execute("DELETE FROM email_codes WHERE email=? AND purpose=?",(email,purpose))
        if purpose=="signup":
            pending=session.get("pending_signup")
            if not pending or pending.get("email")!=email:
                c.commit(); c.close(); flash("Signup expired. Please register again.","error"); return redirect(url_for("register"))
            try:
                cur=c.execute("INSERT INTO users(name,email,password,created_at) VALUES(?,?,?,?)",(pending["name"],email,pending["password"],datetime.now().isoformat()))
                uid=cur.lastrowid
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(uid,"Welcome to Nova Investment","Your email has been verified." ,datetime.now().isoformat()))
            except sqlite3.IntegrityError:
                c.commit(); c.close(); flash("That email already has an account. Please log in.","error"); return redirect(url_for("login"))
        else:
            u=c.execute("SELECT id,is_admin FROM users WHERE lower(email)=lower(?)",(email,)).fetchone()
            if not u:
                c.commit(); c.close(); flash("Account not found. Please log in again.","error"); return redirect(url_for("login"))
            uid=u["id"]; is_admin=u["is_admin"]
        c.commit(); c.close(); session.clear(); session.update(user_id=uid,is_admin=(is_admin if purpose=="login" else 0))
        return redirect(url_for("admin") if session.get("is_admin") else url_for("dashboard"))
    return render_template("verify_email_code.html",email=email,purpose=purpose)

@app.route("/resend-email-code", methods=["POST"])
def resend_email_code():
    email=session.get("verify_email"); purpose=session.get("verify_purpose")
    if not email or purpose not in ("signup","login"):
        return redirect(url_for("login"))
    code=create_email_code(email,purpose)
    if send_verification_email(email,code,purpose): flash("A new verification code was sent.","success")
    else: flash("Could not send code. Please contact the administrator.","error")
    return redirect(url_for("verify_email_code"))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    c=db(); uid=session["user_id"]
    inv=c.execute("SELECT * FROM investments WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()
    tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 12",(uid,)).fetchall()
    notifs=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 6",(uid,)).fetchall()
    today=datetime.now().date().isoformat()
    active_inv=[x for x in inv if x["status"]=="active"]
    # Before maturity, only principal is included in the locked balance.
    # The 12-month profit remains a separate projection until maturity.
    locked_value=sum(x["amount"] for x in active_inv if x["maturity"]>today)
    matured_value=sum(x["target"] for x in active_inv if x["maturity"]<=today)
    approved_deposits=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"]=="Deposit")
    approved_withdrawals=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"] in ("Withdrawal","Redeem"))
    total_balance=max(0,locked_value+matured_value+approved_deposits-approved_withdrawals)
    available_balance=max(0,matured_value+approved_deposits-approved_withdrawals)
    principal=sum(x["amount"] for x in active_inv)
    roi_value=sum(float(x["annual_profit"] or x["roi_amount"] or 0) for x in active_inv)
    projected=total_balance+sum(float(x["annual_profit"] or x["roi_amount"] or 0) for x in active_inv if x["maturity"]>today)
    roi_percent=(roi_value/principal*100) if principal else 0
    c.close()
    return render_template("dashboard.html",investments=inv,transactions=tx,notifications=notifs,total=total_balance,available=available_balance,locked=locked_value,projected=projected,roi_value=roi_value,roi_amount=roi_value,roi_percent=roi_percent)

@app.route("/plans")
def plans(): return render_template("plans.html")

@app.route("/invest/<plan_id>", methods=["GET","POST"])
@login_required
def invest(plan_id):
    p=next((x for x in PLANS if x["id"]==plan_id),None)
    if not p: return redirect(url_for("plans"))
    if request.method=="POST":
        maturity=(datetime.now()+timedelta(days=p["months"]*30)).date().isoformat()
        c=db()
        cur=c.execute("INSERT INTO investments(user_id,plan,amount,target,status,maturity,created_at,monthly_profit,annual_profit,annual_total,roi,roi_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (session["user_id"],p["name"],p["amount"],p["annual_total"],"awaiting_payment",maturity,datetime.now().isoformat(),p["monthly_profit"],p["annual_profit"],p["annual_total"],p["roi"],p["annual_profit"]))
        inv_id=cur.lastrowid
        c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at,investment_id) VALUES(?,?,?,?,?,?,?)",
                  (session["user_id"],"Bitcoin installment",p["initial_payment"],"Awaiting Payment",f"First of 6 monthly installments. Plan value ${p['amount']:,.2f}; installment ${p['initial_payment']:,.2f}; remaining installments subject to schedule.",datetime.now().isoformat(),inv_id))
        c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                  (session["user_id"],"Payment instructions ready",f"Send the first installment of ${p['initial_payment']:,.2f} in Bitcoin. The plan value is ${p['amount']:,.2f}, paid over 6 monthly installments; use the payment confirmation button in Wallet.",datetime.now().isoformat()))
        c.commit(); c.close()
        return redirect(url_for("wallet"))
    roi=p["roi"]
    return render_template("invest.html",plan=p,roi=roi)

@app.route("/wallet",methods=["GET","POST"])
@login_required
def wallet():
    if request.method=="POST":
        kind=request.form.get("kind")
        amount=request.form.get("amount","0")
        try: amount=float(amount)
        except: amount=0
        if amount<=0 or kind not in ("Deposit","Withdrawal","Redeem"):
            flash("Enter a valid amount.","error")
        else:
            c=db()
            if kind in ("Withdrawal","Redeem"):
                uid=session["user_id"]; today=datetime.now().date().isoformat()
                active=c.execute("SELECT * FROM investments WHERE user_id=? AND status='active'",(uid,)).fetchall()
                matured=sum(float(x["target"]) for x in active if x["maturity"]<=today)
                approved_deposits=sum(float(x["amount"]) for x in c.execute("SELECT amount FROM transactions WHERE user_id=? AND status='Approved' AND kind='Deposit'",(uid,)).fetchall())
                approved_withdrawals=sum(float(x["amount"]) for x in c.execute("SELECT amount FROM transactions WHERE user_id=? AND status='Approved' AND kind IN ('Withdrawal','Redeem')",(uid,)).fetchall())
                available_now=max(0,matured+approved_deposits-approved_withdrawals)
                if amount>available_now:
                    c.close(); flash(f"Only ${available_now:,.2f} is currently available to withdraw. Locked investments cannot be withdrawn before maturity except through an approved early-withdrawal request.","error"); return redirect(url_for("wallet"))
            c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at) VALUES(?,?,?,?,?,?)",
                              (session["user_id"],kind,amount,"Pending","Request awaiting administrator review",datetime.now().isoformat()))
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                      (session["user_id"],f"{kind} request received",f"${amount:,.2f} {kind.lower()} request is pending review.",datetime.now().isoformat()))
            c.commit(); c.close(); flash(f"{kind} request submitted.","success")
        return redirect(url_for("wallet"))
    c=db(); uid=session["user_id"]
    tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()
    inv=c.execute("SELECT * FROM investments WHERE user_id=? AND status IN ('awaiting_payment','payment_submitted','active') ORDER BY id DESC",(uid,)).fetchall()
    today=datetime.now().date().isoformat()
    active_inv=[x for x in inv if x["status"]=="active"]
    locked=sum(x["amount"] for x in active_inv if x["maturity"]>today)
    matured=sum(x["target"] for x in active_inv if x["maturity"]<=today)
    deposits=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"]=="Deposit")
    withdrawals=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"] in ("Withdrawal","Redeem"))
    total=max(0,locked+matured+deposits-withdrawals)
    available=max(0,matured+deposits-withdrawals)
    roi_amount=sum(float(x["annual_profit"] or x["roi_amount"] or 0) for x in active_inv)
    c.close()
    return render_template("wallet.html",transactions=tx,investments=inv,balance=total,available=available,locked=locked,roi_amount=roi_amount,nowdate=datetime.now().date().isoformat())

@app.route("/payment/<int:investment_id>",methods=["POST"])
@login_required
def confirm_payment(investment_id):
    c=db(); inv=c.execute("SELECT * FROM investments WHERE id=? AND user_id=?",(investment_id,session["user_id"])).fetchone()
    if not inv:
        c.close(); flash("Investment request not found.","error"); return redirect(url_for("wallet"))
    if inv["status"]!="awaiting_payment":
        c.close(); flash("This payment is already awaiting verification or has been processed.","error"); return redirect(url_for("wallet"))
    submitted_at=datetime.now().isoformat()
    c.execute("UPDATE investments SET status='payment_submitted', payment_submitted_at=? WHERE id=?",(submitted_at,investment_id))
    c.execute("UPDATE transactions SET status='Pending Verification', note=? WHERE investment_id=?",
              ("Customer marked payment as sent; verify the blockchain payment before crediting the account.",investment_id))
    c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
              (session["user_id"],"Payment submitted for verification","Your payment confirmation was received. Please email a screenshot of the payment to "+SUPPORT_EMAIL+". The account is credited only after payment verification." ,datetime.now().isoformat()))
    c.commit(); c.close(); flash("Payment marked as sent. Verification is pending.","success"); return redirect(url_for("wallet"))

@app.route("/early-withdrawal/<int:investment_id>",methods=["POST"])
@login_required
def early_withdrawal(investment_id):
    c=db(); uid=session["user_id"]
    inv=c.execute("SELECT * FROM investments WHERE id=? AND user_id=?",(investment_id,uid)).fetchone()
    if not inv:
        c.close(); flash("Investment not found.","error"); return redirect(url_for("wallet"))
    if inv["status"]!="active":
        c.close(); flash("This investment is not available for early withdrawal.","error"); return redirect(url_for("wallet"))
    if inv["maturity"]<=datetime.now().date().isoformat():
        c.close(); flash("This investment has reached maturity and can be withdrawn through the normal withdrawal process.","success"); return redirect(url_for("wallet"))
    existing=c.execute("SELECT 1 FROM transactions WHERE investment_id=? AND kind='Early Withdrawal' AND status='Pending'",(investment_id,)).fetchone()
    if existing:
        c.close(); flash("An early withdrawal request is already awaiting administrator review.","error"); return redirect(url_for("wallet"))
    # Early withdrawal excludes unearned projected profit; fee is applied to principal only.
    gross=float(inv["amount"]); fee=gross*0.02; payout=gross-fee
    c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at,investment_id) VALUES(?,?,?,?,?,?,?)",
              (uid,"Early Withdrawal",payout,"Pending",f"Early withdrawal requested before maturity. Principal-only gross ${gross:,.2f}; 2% fee ${fee:,.2f}; estimated net payout ${payout:,.2f}. Unmatured projected profit is excluded.",datetime.now().isoformat(),investment_id))
    c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
              (uid,"Early withdrawal request received",f"Your request is under review. A 2% fee (${fee:,.2f}) applies to principal only; estimated net payout is ${payout:,.2f}. Unmatured projected profit is excluded. Please contact {SUPPORT_EMAIL} for assistance.",datetime.now().isoformat()))
    c.commit(); c.close(); flash("Early withdrawal request sent to the administrator. The 2% fee has been included in the request.","success"); return redirect(url_for("wallet"))

@app.route("/support/data")
@login_required
def support_data():
    c=db()
    rows=c.execute("SELECT id,message,reply,created_at FROM support WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    c.close()
    return jsonify({"messages":[dict(r) for r in rows]})

@app.route("/admin/support-data")
@admin_required
def admin_support_data():
    c=db()
    rows=c.execute("""SELECT support.id,support.user_id,support.message,support.reply,support.created_at,users.name,users.email
                     FROM support JOIN users ON users.id=support.user_id ORDER BY support.id DESC""").fetchall()
    c.close()
    return jsonify({"messages":[dict(r) for r in rows]})

@app.route("/support",methods=["GET","POST"])
@login_required
def support():
    if request.method=="POST":
        msg=request.form.get("message","").strip()
        if msg:
            c=db(); c.execute("INSERT INTO support(user_id,message,created_at) VALUES(?,?,?)",
                              (session["user_id"],msg,datetime.now().isoformat())); c.commit(); c.close()
            flash("Message sent to customer support.","success")
        return redirect(url_for("support"))
    c=db(); msgs=c.execute("SELECT * FROM support WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall(); c.close()
    return render_template("support.html",messages=msgs)

@app.route("/security")
@login_required
def security(): return render_template("security.html")

@app.route("/statement")
@login_required
def statement():
    c=db(); tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall(); c.close()
    return render_template("statement.html",transactions=tx)

@app.route("/notifications")
@login_required
def notifications():
    c=db(); rows=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall(); c.close()
    return render_template("notifications.html",notifications=rows)

@app.route("/admin")
@admin_required
def admin():
    c=db()
    users=c.execute("SELECT id,name,email,created_at FROM users ORDER BY id DESC").fetchall()
    inv=c.execute("""SELECT investments.*,users.name,users.email FROM investments
                     JOIN users ON users.id=investments.user_id ORDER BY investments.id DESC""").fetchall()
    tx=c.execute("""SELECT transactions.*,users.name,users.email FROM transactions
                    JOIN users ON users.id=transactions.user_id ORDER BY transactions.id DESC""").fetchall()
    messages=c.execute("""SELECT support.*,users.name,users.email FROM support
                          JOIN users ON users.id=support.user_id ORDER BY support.id DESC""").fetchall()
    c.close()
    return render_template("admin.html",users=users,investments=inv,transactions=tx,messages=messages)

@app.route("/admin/action",methods=["POST"])
@admin_required
def admin_action():
    typ=request.form.get("type", "")
    rid=request.form.get("id", "")
    action=request.form.get("action", "")
    if typ not in ("investment", "early_withdrawal", "transaction", "support"):
        flash("Invalid administrator action.", "error")
        return redirect(url_for("admin"))
    if not rid.isdigit() or action not in ("", "approve", "reject"):
        flash("Invalid administrator request.", "error")
        return redirect(url_for("admin"))
    c=db()
    if typ=="investment":
        inv=c.execute("SELECT * FROM investments WHERE id=?",(int(rid),)).fetchone()
        if inv:
            if action == "approve" and inv["status"] != "payment_submitted":
                c.close()
                flash("An investment can only be approved after the customer submits payment for verification.", "error")
                return redirect(url_for("admin"))
            if inv["status"] not in ("awaiting_payment", "payment_submitted"):
                c.close()
                flash("This investment has already been processed.", "error")
                return redirect(url_for("admin"))
            new_status="active" if action=="approve" else "rejected"
            c.execute("UPDATE investments SET status=? WHERE id=?",(new_status,rid))
            if action=="approve":
                tx=c.execute("SELECT id FROM transactions WHERE investment_id=? AND kind='Bitcoin payment'",(inv["id"],)).fetchone()
                if tx: c.execute("UPDATE transactions SET status='Approved', note=? WHERE id=?",("Bitcoin payment verified by administrator; original principal is credited and locked. Projected profit is not a guaranteed payment.",tx["id"]))
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(inv["user_id"],"Investment approved",f"Your {inv['plan']} investment was approved. Principal ${inv['amount']:,.2f} is locked until {inv['maturity']}. The 12-month projected profit is ${float(inv['annual_profit'] or inv['roi_amount'] or 0):,.2f}; it is a projection, not a guaranteed return, and is not included in your available balance before maturity.",datetime.now().isoformat()))
            else:
                c.execute("UPDATE transactions SET status='Rejected', note=? WHERE investment_id=? AND status IN ('Pending Verification','Awaiting Payment')",("Payment could not be verified.",inv["id"]))
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(inv["user_id"],"Payment review update","The submitted payment could not be verified. Please contact support if you believe this is an error.",datetime.now().isoformat()))
    elif typ=="early_withdrawal":
        tx=c.execute("SELECT * FROM transactions WHERE id=? AND kind='Early Withdrawal'",(rid,)).fetchone()
        if tx:
            inv=c.execute("SELECT * FROM investments WHERE id=?",(tx["investment_id"],)).fetchone() if tx["investment_id"] else None
            if action=="approve" and inv:
                c.execute("UPDATE transactions SET status='Approved' WHERE id=?",(tx["id"],))
                c.execute("UPDATE investments SET status='withdrawn' WHERE id=?",(inv["id"],))
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(tx["user_id"],"Early withdrawal approved",f"Your early withdrawal was approved. The 2% fee was applied and the net payout is ${tx['amount']:,.2f}.",datetime.now().isoformat()))
            elif action=="reject":
                c.execute("UPDATE transactions SET status='Rejected' WHERE id=?",(tx["id"],))
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(tx["user_id"],"Early withdrawal update","Your early withdrawal request was not approved. Please contact support for more information.",datetime.now().isoformat()))
    elif typ=="transaction":
        tx=c.execute("SELECT * FROM transactions WHERE id=?",(rid,)).fetchone()
        if tx:
            new_status="Approved" if action=="approve" else "Rejected"
            c.execute("UPDATE transactions SET status=? WHERE id=?",(new_status,rid))
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(tx["user_id"],"Transaction update",f"Your {tx['kind'].lower()} request is now {new_status.lower()}.",datetime.now().isoformat()))
    elif typ=="support":
        reply=request.form.get("reply","").strip()
        if not reply or len(reply) > 5000:
            c.close()
            flash("Please enter a support reply of 1–5000 characters.", "error")
            return redirect(url_for("admin"))
        c.execute("UPDATE support SET reply=? WHERE id=?",(reply,int(rid)))
    c.commit(); c.close(); return redirect(url_for("admin"))

@app.route("/api/btc")
def btc():
    # Front-end may query a public market API. This endpoint intentionally does not invent a rate.
    return jsonify({"source":"frontend market-data service","message":"Use the market-data provider from the notification script."})

@app.route("/health")
def health(): return "Nova Investment application is running."

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
