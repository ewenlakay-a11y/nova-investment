
import os, sqlite3, secrets, smtplib, ssl
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nova-investment-project-secret-change-me")
DB = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "nova_investment.db"))

BTC_ADDRESS = "bc1qwe7l499zq0y2l7x59lvnwlft02v0dhzh34q5jy"
SUPPORT_EMAIL = "novainvestment965@gmail.com"
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", SUPPORT_EMAIL)
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
OTP_MINUTES = 10

PLANS = [
    {"id":"starter","name":"Starter","amount":500,"roi":40,"months":12},
    {"id":"standard","name":"Standard","amount":1000,"roi":40,"months":12},
    {"id":"growth","name":"Growth","amount":3000,"roi":45,"months":12},
    {"id":"plus","name":"Plus","amount":5000,"roi":45,"months":12},
    {"id":"pro","name":"Pro","amount":10000,"roi":50,"months":12},
    {"id":"elite","name":"Elite","amount":20000,"roi":55,"months":12},
]

# These are user-facing plan projections, not guaranteed investment returns.
for _p in PLANS:
    _p["roi_amount"] = _p["amount"] * (_p["roi"] / 100)
    _p["annual_contribution"] = _p["amount"]
    _p["annual_profit"] = _p["roi_amount"]
    _p["annual_total"] = _p["amount"] + _p["roi_amount"]
    _p["target"] = _p["annual_total"]

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
      email_verified INTEGER DEFAULT 0,
      email_otp_hash TEXT, email_otp_expires TEXT,
      login_otp_hash TEXT, login_otp_expires TEXT,
      reset_otp_hash TEXT, reset_otp_expires TEXT,
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
    cols=[r[1] for r in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if "investment_id" not in cols:
        c.execute("ALTER TABLE transactions ADD COLUMN investment_id INTEGER")
    usercols=[r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    for col,typ in [("email_verified","INTEGER DEFAULT 0"),("email_otp_hash","TEXT"),("email_otp_expires","TEXT"),("login_otp_hash","TEXT"),("login_otp_expires","TEXT"),("reset_otp_hash","TEXT"),("reset_otp_expires","TEXT"),("withdraw_otp_hash","TEXT"),("withdraw_otp_expires","TEXT")]:
        if col not in usercols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    txcols=[r[1] for r in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if "wallet_address" not in txcols:
        c.execute("ALTER TABLE transactions ADD COLUMN wallet_address TEXT")
    invcols=[r[1] for r in c.execute("PRAGMA table_info(investments)").fetchall()]
    if "payment_submitted_at" not in invcols:
        c.execute("ALTER TABLE investments ADD COLUMN payment_submitted_at TEXT")
    for col,typ in [("monthly_profit","REAL"),("annual_profit","REAL"),("annual_total","REAL"),("roi","REAL"),("roi_amount","REAL")]:
        if col not in invcols:
            c.execute(f"ALTER TABLE investments ADD COLUMN {col} {typ}")
    # Demo/admin accounts for assessment
    if not c.execute("SELECT 1 FROM users WHERE email=?",("demo@novainvestment.local",)).fetchone():
        c.execute("INSERT INTO users(name,email,password,is_admin,email_verified,created_at) VALUES(?,?,?,?,?,?)",
                  ("Demo Investor","demo@novainvestment.local",generate_password_hash("demo1234"),0,1,datetime.now().isoformat()))
    if not c.execute("SELECT 1 FROM users WHERE email=?",("admin@novainvestment.local",)).fetchone():
        c.execute("INSERT INTO users(name,email,password,is_admin,email_verified,created_at) VALUES(?,?,?,?,?,?)",
                  ("Nova Administrator","admin@novainvestment.local",generate_password_hash("admin1234"),1,1,datetime.now().isoformat()))
    # Existing assessment accounts are trusted for the demo environment.
    c.execute("UPDATE users SET email_verified=1 WHERE email IN (?,?)",("demo@novainvestment.local","admin@novainvestment.local"))
    c.commit(); c.close()

def send_email(to_email, subject, body):
    if not SMTP_PASSWORD:
        raise RuntimeError("Email delivery is not configured. Add SMTP_PASSWORD to the Render environment variables.")
    msg=EmailMessage()
    msg["Subject"]=subject
    msg["From"]=SMTP_FROM
    msg["To"]=to_email
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)

def make_code():
    return f"{secrets.randbelow(1000000):06d}"

def set_otp(c, uid, kind):
    code=make_code()
    h=generate_password_hash(code)
    exp=(datetime.now()+timedelta(minutes=OTP_MINUTES)).isoformat()
    c.execute(f"UPDATE users SET {kind}_otp_hash=?, {kind}_otp_expires=? WHERE id=?",(h,exp,uid))
    return code

def otp_valid(user_row, kind, code):
    if not code or not user_row[f"{kind}_otp_hash"] or not user_row[f"{kind}_otp_expires"]:
        return False
    try:
        if datetime.fromisoformat(user_row[f"{kind}_otp_expires"]) < datetime.now():
            return False
    except ValueError:
        return False
    return check_password_hash(user_row[f"{kind}_otp_hash"], code)

def clear_otp(c, uid, kind):
    c.execute(f"UPDATE users SET {kind}_otp_hash=NULL, {kind}_otp_expires=NULL WHERE id=?",(uid,))

def password_ok(stored, supplied):
    try:
        return check_password_hash(stored, supplied)
    except Exception:
        return secrets.compare_digest(stored, supplied)

def ensure_hashed_password(c, uid, stored, supplied):
    if not stored.startswith(("scrypt:","pbkdf2:")):
        c.execute("UPDATE users SET password=? WHERE id=?",(generate_password_hash(supplied),uid))

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
    return {"current_user":user(),"btc_address":BTC_ADDRESS,"support_email":SUPPORT_EMAIL,"plans":PLANS}

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/activity-data")
def activity_data():
    c=db()
    rows=c.execute("""SELECT users.name, investments.amount, investments.target, investments.created_at FROM investments JOIN users ON users.id=investments.user_id WHERE investments.status='active' ORDER BY investments.id DESC LIMIT 12""").fetchall()
    c.close()
    activities=[]
    for r in rows:
        name=(r['name'] or 'Customer').strip().split()[0]
        masked=(name[:1]+"***") if name else "Customer"
        activities.append({"label":f"{masked} has an approved {r['amount']:,.0f} investment · portfolio value ${r['target']:,.0f}"})
    return jsonify({"activities":activities})

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if not name or not email or len(password)<8:
            flash("Please complete all fields. Password must contain at least 8 characters.","error")
            return redirect(url_for("register"))
        c=db()
        try:
            if c.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)",(email,)).fetchone():
                c.close(); flash("That email already has an account. Please use a different email or log in.","error"); return redirect(url_for("register"))
            cur=c.execute("INSERT INTO users(name,email,password,created_at) VALUES(?,?,?,?)",
                          (name,email,generate_password_hash(password),datetime.now().isoformat()))
            uid=cur.lastrowid
            code=set_otp(c,uid,"email")
            try:
                send_email(email,"Nova Investment email verification code",f"Your Nova Investment verification code is {code}. It expires in {OTP_MINUTES} minutes. If you did not create this account, ignore this email.")
            except Exception as exc:
                c.execute("DELETE FROM users WHERE id=?",(uid,)); c.commit(); c.close()
                flash("We could not send the verification email. Please try again later or contact support.","error")
                return redirect(url_for("register"))
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                      (uid,"Verify your email","A verification code was sent to your email address.",datetime.now().isoformat()))
            c.commit(); c.close()
            session["pending_verify_user"]=uid
            flash("We sent a 6-digit verification code to your email.","success")
            return redirect(url_for("verify_email"))
        except sqlite3.IntegrityError:
            c.close(); flash("An account with that email already exists.","error")
    return render_template("register.html")

@app.route("/verify-email", methods=["GET","POST"])
def verify_email():
    uid=session.get("pending_verify_user")
    if not uid: return redirect(url_for("register"))
    c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    if not u: c.close(); session.pop("pending_verify_user",None); return redirect(url_for("register"))
    if request.method=="POST":
        code=request.form.get("code","").strip()
        if otp_valid(u,"email",code):
            c.execute("UPDATE users SET email_verified=1 WHERE id=?",(uid,)); clear_otp(c,uid,"email"); c.commit(); c.close()
            session.pop("pending_verify_user",None); session.update(user_id=uid,is_admin=0)
            return redirect(url_for("dashboard"))
        c.close(); flash("That code is invalid or expired.","error")
    else: c.close()
    return render_template("verify_code.html",title="Verify your email",heading="Verify your email",description="Enter the 6-digit code sent to your email address.",action=url_for("verify_email"),button="Verify email")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        c=db(); u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if u and password_ok(u["password"],password):
            ensure_hashed_password(c,u["id"],u["password"],password)
            if not u["email_verified"]:
                code=set_otp(c,u["id"],"email")
                try: send_email(u["email"],"Nova Investment email verification code",f"Your verification code is {code}. It expires in {OTP_MINUTES} minutes.")
                except Exception: c.close(); flash("We could not send the verification email. Please contact support.","error"); return redirect(url_for("login"))
                c.commit(); c.close(); session["pending_verify_user"]=u["id"]; return redirect(url_for("verify_email"))
            code=set_otp(c,u["id"],"login")
            try:
                send_email(u["email"],"Nova Investment sign-in code",f"Your Nova Investment sign-in code is {code}. It expires in {OTP_MINUTES} minutes. If you did not try to sign in, change your password and contact support.")
            except Exception:
                c.close(); flash("We could not send your sign-in code. Please contact support.","error"); return redirect(url_for("login"))
            c.commit(); c.close(); session["pending_login_user"]=u["id"]
            return redirect(url_for("verify_login"))
        c.close(); flash("Invalid login details.","error")
    return render_template("login.html")

@app.route("/verify-login", methods=["GET","POST"])
def verify_login():
    uid=session.get("pending_login_user")
    if not uid: return redirect(url_for("login"))
    c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    if not u: c.close(); session.pop("pending_login_user",None); return redirect(url_for("login"))
    if request.method=="POST":
        if otp_valid(u,"login",request.form.get("code","").strip()):
            clear_otp(c,uid,"login"); c.commit(); c.close(); session.pop("pending_login_user",None); session.update(user_id=uid,is_admin=u["is_admin"]); return redirect(url_for("admin") if u["is_admin"] else url_for("dashboard"))
        flash("That sign-in code is invalid or expired.","error")
    c.close()
    return render_template("verify_code.html",title="Sign-in verification",heading="Confirm your sign-in",description="Enter the 6-digit code sent to your email address.",action=url_for("verify_login"),button="Sign in")

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); c=db(); u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
        if u:
            code=set_otp(c,u["id"],"reset")
            try:
                send_email(email,"Nova Investment password reset code",f"Your password reset code is {code}. It expires in {OTP_MINUTES} minutes. If you did not request this, ignore this email.")
                c.commit(); c.close(); session["pending_reset_email"]=email; flash("If that email is registered, a reset code has been sent.","success"); return redirect(url_for("reset_password"))
            except Exception:
                c.close(); flash("We could not send the reset email. Please contact support.","error"); return redirect(url_for("forgot_password"))
        c.close(); flash("If that email is registered, a reset code has been sent.","success"); return redirect(url_for("forgot_password"))
    return render_template("forgot_password.html")

@app.route("/reset-password", methods=["GET","POST"])
def reset_password():
    email=session.get("pending_reset_email")
    if not email: return redirect(url_for("forgot_password"))
    if request.method=="POST":
        code=request.form.get("code","").strip(); password=request.form.get("password","")
        if len(password)<8: flash("Password must contain at least 8 characters.","error")
        else:
            c=db(); u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
            if u and otp_valid(u,"reset",code):
                c.execute("UPDATE users SET password=? WHERE id=?",(generate_password_hash(password),u["id"])); clear_otp(c,u["id"],"reset"); c.commit(); c.close(); session.pop("pending_reset_email",None); flash("Your password has been reset. You can now sign in.","success"); return redirect(url_for("login"))
            c.close(); flash("That code is invalid or expired.","error")
    return render_template("reset_password.html",email=email)

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
    locked_value=sum(x["target"] for x in active_inv if x["maturity"]>today)
    matured_value=sum(x["target"] for x in active_inv if x["maturity"]<=today)
    approved_deposits=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"]=="Deposit")
    approved_withdrawals=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"] in ("Withdrawal","Redeem","Early Withdrawal"))
    total_balance=max(0,locked_value+matured_value+approved_deposits-approved_withdrawals)
    available_balance=max(0,matured_value+approved_deposits-approved_withdrawals)
    principal=sum(x["amount"] for x in active_inv)
    projected=sum(x["target"] for x in active_inv)
    roi_value=max(0,projected-principal)
    roi_percent=(roi_value/principal*100) if principal else 0
    c.close()
    return render_template("dashboard.html",investments=inv,transactions=tx,notifications=notifs,total=total_balance,available=available_balance,locked=locked_value,projected=projected,roi_value=roi_value,roi_percent=roi_percent)

@app.route("/plans")
def plans():
    return render_template("plans.html", plans=PLANS)

@app.route("/invest/<plan_id>", methods=["GET","POST"])
@login_required
def invest(plan_id):
    p=next((x for x in PLANS if x["id"]==plan_id),None)
    if not p: return redirect(url_for("plans"))
    if request.method=="POST":
        maturity=(datetime.now()+timedelta(days=p["months"]*30)).date().isoformat()
        c=db()
        cur=c.execute("INSERT INTO investments(user_id,plan,amount,target,status,maturity,created_at,monthly_profit,annual_profit,annual_total,roi,roi_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (session["user_id"],p["name"],p["amount"],p["annual_total"],"awaiting_payment",maturity,datetime.now().isoformat(),p["roi_amount"],p["roi_amount"],p["annual_total"],p["roi"],p["roi_amount"]))
        inv_id=cur.lastrowid
        c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at,investment_id) VALUES(?,?,?,?,?,?,?)",
                  (session["user_id"],"Bitcoin payment",p["amount"],"Awaiting Payment","Investment payment awaiting customer confirmation",datetime.now().isoformat(),inv_id))
        c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                  (session["user_id"],"Payment instructions ready",f"Send ${p['amount']:,.0f} in Bitcoin, then use the payment confirmation button in Wallet.",datetime.now().isoformat()))
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
                approved_withdrawals=sum(float(x["amount"]) for x in c.execute("SELECT amount FROM transactions WHERE user_id=? AND status='Approved' AND kind IN ('Withdrawal','Redeem','Early Withdrawal')",(uid,)).fetchall())
                available_now=max(0,matured+approved_deposits-approved_withdrawals)
                if amount>available_now:
                    c.close(); flash(f"Only ${available_now:,.2f} is currently available to withdraw. Locked investments cannot be withdrawn before maturity except through an approved early-withdrawal request.","error"); return redirect(url_for("wallet"))
            if kind in ("Withdrawal","Redeem"):
                wallet_address=request.form.get("wallet_address","").strip()
                code=request.form.get("code","").strip()
                u=c.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
                if not wallet_address:
                    c.close(); flash("Enter the wallet address where you want the approved withdrawal sent.","error"); return redirect(url_for("wallet"))
                if not u["email_verified"]:
                    c.close(); flash("Verify your email before requesting a withdrawal.","error"); return redirect(url_for("wallet"))
                if not otp_valid(u,"withdraw",code):
                    code_value=set_otp(c,session["user_id"],"withdraw")
                    try:
                        send_email(u["email"],"Nova Investment withdrawal verification code",f"Your withdrawal verification code is {code_value}. It expires in {OTP_MINUTES} minutes. If you did not request a withdrawal, contact support immediately.")
                        c.commit(); c.close(); flash("We sent a withdrawal verification code to your email. Enter the code in the withdrawal form and submit again.","success"); return redirect(url_for("wallet"))
                    except Exception:
                        c.close(); flash("We could not send the withdrawal verification email. Please contact support.","error"); return redirect(url_for("wallet"))
                clear_otp(c,session["user_id"],"withdraw")
            else:
                wallet_address=None
            c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at,wallet_address) VALUES(?,?,?,?,?,?,?)",
                              (session["user_id"],kind,amount,"Pending","Request awaiting administrator review",datetime.now().isoformat(),wallet_address))
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                      (session["user_id"],f"{kind} request received",f"${amount:,.2f} {kind.lower()} request is pending review.",datetime.now().isoformat()))
            c.commit(); c.close(); flash(f"{kind} request submitted.","success")
        return redirect(url_for("wallet"))
    c=db(); uid=session["user_id"]
    tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()
    inv=c.execute("SELECT * FROM investments WHERE user_id=? AND status IN ('awaiting_payment','payment_submitted','active') ORDER BY id DESC",(uid,)).fetchall()
    today=datetime.now().date().isoformat()
    active_inv=[x for x in inv if x["status"]=="active"]
    locked=sum(x["target"] for x in active_inv if x["maturity"]>today)
    matured=sum(x["target"] for x in active_inv if x["maturity"]<=today)
    deposits=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"]=="Deposit")
    withdrawals=sum(x["amount"] for x in tx if x["status"]=="Approved" and x["kind"] in ("Withdrawal","Redeem","Early Withdrawal"))
    total=max(0,locked+matured+deposits-withdrawals)
    available=max(0,matured+deposits-withdrawals)
    roi_amount=sum(float(x["roi_amount"] or x["annual_profit"] or 0) for x in active_inv)
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
    wallet_address=request.form.get("wallet_address","").strip(); code=request.form.get("code","").strip()
    u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    if not wallet_address:
        c.close(); flash("Enter the wallet address where you want the approved early withdrawal sent.","error"); return redirect(url_for("wallet"))
    if not otp_valid(u,"withdraw",code):
        code_value=set_otp(c,uid,"withdraw")
        try:
            send_email(u["email"],"Nova Investment withdrawal verification code",f"Your withdrawal verification code is {code_value}. It expires in {OTP_MINUTES} minutes. If you did not request a withdrawal, contact support immediately.")
            c.commit(); c.close(); flash("We sent a withdrawal verification code to your email. Enter it in the withdrawal form and submit again.","success"); return redirect(url_for("wallet"))
        except Exception:
            c.close(); flash("We could not send the withdrawal verification email. Please contact support.","error"); return redirect(url_for("wallet"))
    clear_otp(c,uid,"withdraw")
    existing=c.execute("SELECT 1 FROM transactions WHERE investment_id=? AND kind='Early Withdrawal' AND status='Pending'",(investment_id,)).fetchone()
    if existing:
        c.close(); flash("An early withdrawal request is already awaiting administrator review.","error"); return redirect(url_for("wallet"))
    gross=float(inv["amount"]); fee=gross*0.02; payout=gross-fee
    c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at,investment_id,wallet_address) VALUES(?,?,?,?,?,?,?,?)",
              (uid,"Early Withdrawal",payout,"Pending",f"Early withdrawal requested before maturity. Gross ${gross:,.2f}; 2% early withdrawal fee ${fee:,.2f}; net payout ${payout:,.2f}.",datetime.now().isoformat(),investment_id,wallet_address))
    c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
              (uid,"Early withdrawal request received",f"Your request is under review. A 2% early withdrawal fee (${fee:,.2f}) applies; estimated net payout is ${payout:,.2f}.",datetime.now().isoformat()))
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
    typ=request.form.get("type"); rid=request.form.get("id"); action=request.form.get("action")
    c=db()
    if typ=="investment":
        inv=c.execute("SELECT * FROM investments WHERE id=?",(rid,)).fetchone()
        if inv:
            new_status="active" if action=="approve" else "rejected"
            c.execute("UPDATE investments SET status=? WHERE id=?",(new_status,rid))
            if action=="approve":
                tx=c.execute("SELECT id FROM transactions WHERE investment_id=? AND kind='Bitcoin payment'",(inv["id"],)).fetchone()
                if tx: c.execute("UPDATE transactions SET status='Approved', note=? WHERE id=?",("Bitcoin payment verified by administrator; principal recorded. Projected ROI is not credited as cash.",tx["id"]))
                c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",(inv["user_id"],"Investment credited",f"Your {inv['plan']} investment was approved. Your principal of ${inv['amount']:,.2f} is recorded and remains locked until {inv['maturity']}. Any ROI shown is a projection, not a guaranteed or currently credited amount.",datetime.now().isoformat()))
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
        c.execute("UPDATE support SET reply=? WHERE id=?",(reply,rid))
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
