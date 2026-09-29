
import os, sqlite3, secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nova-investment-project-secret-change-me")
DB = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "nova_investment.db"))

BTC_ADDRESS = "bc1qwe7l499zq0y2l7x59lvnwlft02v0dhzh34q5jy"
SUPPORT_EMAIL = "novainvestment965@gmail.com"

PLANS = [
    {"id":"starter","name":"Starter","amount":500,"target":3500,"months":12},
    {"id":"standard","name":"Standard","amount":1000,"target":5500,"months":12},
    {"id":"premium","name":"Premium","amount":5000,"target":10500,"months":12},
    {"id":"elite","name":"Elite","amount":10000,"target":30000,"months":12},
]

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
    # Demo/admin accounts for assessment
    if not c.execute("SELECT 1 FROM users WHERE email=?",("demo@novainvestment.local",)).fetchone():
        c.execute("INSERT INTO users(name,email,password,is_admin,created_at) VALUES(?,?,?,?,?)",
                  ("Demo Investor","demo@novainvestment.local","demo1234",0,datetime.now().isoformat()))
    if not c.execute("SELECT 1 FROM users WHERE email=?",("admin@novainvestment.local",)).fetchone():
        c.execute("INSERT INTO users(name,email,password,is_admin,created_at) VALUES(?,?,?,?,?)",
                  ("Nova Administrator","admin@novainvestment.local","admin1234",1,datetime.now().isoformat()))
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
    return {"current_user":user(),"btc_address":BTC_ADDRESS,"support_email":SUPPORT_EMAIL,"plans":PLANS}

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if not name or not email or len(password)<6:
            flash("Please complete all fields. Password must contain at least 6 characters.","error")
            return redirect(url_for("register"))
        c=db()
        try:
            cur=c.execute("INSERT INTO users(name,email,password,created_at) VALUES(?,?,?,?)",
                          (name,email,password,datetime.now().isoformat()))
            uid=cur.lastrowid
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                      (uid,"Welcome to Nova Investment","Your account has been created successfully.",datetime.now().isoformat()))
            c.commit()
            session.update(user_id=uid,is_admin=0)
            c.close()
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            c.close(); flash("An account with that email already exists.","error")
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        c=db(); u=c.execute("SELECT * FROM users WHERE email=? AND password=?",(email,password)).fetchone(); c.close()
        if u:
            session.update(user_id=u["id"],is_admin=u["is_admin"])
            return redirect(url_for("admin") if u["is_admin"] else url_for("dashboard"))
        flash("Invalid login details.","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    c=db(); uid=session["user_id"]
    inv=c.execute("SELECT * FROM investments WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall()
    tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 8",(uid,)).fetchall()
    notifs=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 6",(uid,)).fetchall()
    total=sum(x["amount"] for x in inv if x["status"] in ("pending","active"))
    projected=sum(x["target"] for x in inv if x["status"] in ("pending","active"))
    c.close()
    return render_template("dashboard.html",investments=inv,transactions=tx,notifications=notifs,total=total,projected=projected)

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
        c.execute("""INSERT INTO investments(user_id,plan,amount,target,status,maturity,created_at)
                     VALUES(?,?,?,?,?,?,?)""",(session["user_id"],p["name"],p["amount"],p["target"],"pending",maturity,datetime.now().isoformat()))
        c.execute("""INSERT INTO transactions(user_id,kind,amount,status,note,created_at)
                     VALUES(?,?,?,?,?,?)""",(session["user_id"],"Investment request",p["amount"],"Pending","Awaiting Bitcoin payment verification",datetime.now().isoformat()))
        c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                  (session["user_id"],"Investment request received",f"{p['name']} plan request for ${p['amount']:,.0f} was received.",datetime.now().isoformat()))
        c.commit(); c.close()
        flash("Investment request created. Follow the Bitcoin payment instructions to complete the request.","success")
        return redirect(url_for("wallet"))
    roi=(p["target"]-p["amount"])/p["amount"]*100
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
            c.execute("INSERT INTO transactions(user_id,kind,amount,status,note,created_at) VALUES(?,?,?,?,?,?)",
                      (session["user_id"],kind,amount,"Pending","Request awaiting administrator review",datetime.now().isoformat()))
            c.execute("INSERT INTO notifications(user_id,title,message,created_at) VALUES(?,?,?,?)",
                      (session["user_id"],f"{kind} request received",f"${amount:,.2f} {kind.lower()} request is pending review.",datetime.now().isoformat()))
            c.commit(); c.close()
            flash(f"{kind} request submitted.","success")
        return redirect(url_for("wallet"))
    c=db(); tx=c.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall(); c.close()
    return render_template("wallet.html",transactions=tx)

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
        c.execute("UPDATE investments SET status=? WHERE id=?",("active" if action=="approve" else "rejected",rid))
    elif typ=="transaction":
        c.execute("UPDATE transactions SET status=? WHERE id=?",("Approved" if action=="approve" else "Rejected",rid))
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
