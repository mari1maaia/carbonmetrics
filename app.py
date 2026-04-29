from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from datetime import datetime, date
import secrets, string, os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
# Em produção usa PostgreSQL se DATABASE_URL estiver configurado, senão SQLite local
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///carbonmetric.db").replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ─── CONFIGURAÇÃO DE E-MAIL ─────────────────────────────────────────────────
# Edite as linhas abaixo com os dados da sua conta de e-mail
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")  # seu e-mail
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")  # senha de app
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME", "noreply@carbonmetric.com")

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para continuar."

def send_email(to, subject, body):
    """Envia e-mail; retorna True se ok, False se não configurado."""
    if not app.config["MAIL_USERNAME"]:
        return False
    try:
        msg = Message(subject, recipients=[to], body=body)
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False

# ─── MODELS ──────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="client")
    job_title = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    scope_access = db.Column(db.String(10), nullable=True, default="123")  # ex: "12", "123", "1"
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)
    company = db.relationship("Company", back_populates="users", foreign_keys="User.company_id")

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(20), nullable=True)
    sector = db.Column(db.String(100), nullable=True)
    responsible_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    status = db.Column(db.String(30), default="em_andamento")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users = db.relationship("User", back_populates="company", foreign_keys="User.company_id")
    emissions = db.relationship("EmissionEntry", back_populates="company", cascade="all, delete-orphan")
    activities = db.relationship("ActivityLog", back_populates="company", cascade="all, delete-orphan")

class EmissionEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    inventory_year = db.Column(db.Integer, nullable=False, default=2024)
    scope = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    source_name = db.Column(db.String(200), nullable=False)
    fuel_type = db.Column(db.String(100), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(30), nullable=False)
    emission_factor = db.Column(db.Float, nullable=False)
    ef_source = db.Column(db.String(50), default="MCTI 2024")
    total_co2e = db.Column(db.Float, nullable=False)
    period = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    attachment_note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    company = db.relationship("Company", back_populates="emissions")

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(300), nullable=False)
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    company = db.relationship("Company", back_populates="activities")
    user = db.relationship("User")

class Invite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ─── FATORES DE EMISSÃO ──────────────────────────────────────────────────────

EMISSION_FACTORS = [
    {"id":1,"name":"Gás natural","scope":1,"category":"Combustão estacionária","fe":2.02,"unit":"m³","source":"MCTI 2024"},
    {"id":2,"name":"Diesel","scope":1,"category":"Combustão móvel","fe":2.68,"unit":"L","source":"MCTI 2024"},
    {"id":3,"name":"Gasolina","scope":1,"category":"Combustão móvel","fe":2.27,"unit":"L","source":"MCTI 2024"},
    {"id":4,"name":"GLP","scope":1,"category":"Combustão estacionária","fe":3.01,"unit":"kg","source":"MCTI 2024"},
    {"id":5,"name":"Etanol hidratado","scope":1,"category":"Combustão móvel","fe":1.46,"unit":"L","source":"MCTI 2024"},
    {"id":6,"name":"Carvão mineral","scope":1,"category":"Combustão estacionária","fe":2.54,"unit":"kg","source":"SEEG v12"},
    {"id":7,"name":"R-22 (HFC)","scope":1,"category":"Emissões fugitivas","fe":1810.0,"unit":"kg","source":"IPCC AR6"},
    {"id":8,"name":"R-410A (HFC)","scope":1,"category":"Emissões fugitivas","fe":2088.0,"unit":"kg","source":"IPCC AR6"},
    {"id":9,"name":"Energia elétrica SIN","scope":2,"category":"Eletricidade comprada (localização)","fe":0.0408,"unit":"kWh","source":"MCTI 2024"},
    {"id":10,"name":"Transporte rodoviário (carga)","scope":3,"category":"Cat. 4 — Transporte upstream","fe":0.089,"unit":"t·km","source":"SEEG v12"},
    {"id":11,"name":"Viagem aérea nacional","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.158,"unit":"pkm","source":"IPCC AR6"},
    {"id":12,"name":"Resíduos sólidos (aterro)","scope":3,"category":"Cat. 5 — Resíduos","fe":0.52,"unit":"kg","source":"MCTI 2024"},
]

CURRENT_YEARS = list(range(date.today().year, 2014, -1))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def log_activity(action, detail=None, company_id=None):
    entry = ActivityLog(
        company_id=company_id or (current_user.company_id if current_user.is_authenticated else None),
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action, detail=detail
    )
    db.session.add(entry)
    db.session.commit()

def get_years_for_company(company_id):
    rows = db.session.query(EmissionEntry.inventory_year).filter_by(company_id=company_id).distinct().order_by(EmissionEntry.inventory_year.desc()).all()
    return [r[0] for r in rows]

# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard") if current_user.role == "admin" else url_for("client_dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email, active=True).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            log_activity("Login realizado")
            return redirect(url_for("index"))
        flash("E-mail ou senha incorretos.", "error")
    return render_template("login.html")


@app.route("/esqueci-senha", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, active=True).first()
        if not user:
            flash("E-mail não encontrado. Verifique se está correto ou contate a Interbio.", "error")
            return render_template("forgot_password.html")
        # Gerar senha temporária
        chars = string.ascii_letters + string.digits
        temp_pw = "".join(secrets.choice(chars) for _ in range(10))
        user.password_hash = bcrypt.generate_password_hash(temp_pw).decode("utf-8")
        db.session.commit()
        # Tentar enviar e-mail
        body = f"""Olá, {user.name}!

Você solicitou a recuperação de acesso ao CarbonMetric - Interbio Tecnologia Ambiental.

Sua senha temporária é: {temp_pw}

Acesse o sistema em: http://localhost:5000
Após entrar, recomendamos alterar sua senha.

Em caso de dúvidas, entre em contato com a Interbio.

Atenciosamente,
Equipe Interbio Tecnologia Ambiental"""
        sent = send_email(email, "CarbonMetric — Senha temporária", body)
        log_activity(f"Recuperação de senha solicitada para {email}")
        return render_template("forgot_password.html", success=True, sent=sent, temp_pw=temp_pw if not sent else None)
    return render_template("forgot_password.html")

@app.route("/logout")
@login_required
def logout():
    log_activity("Logout realizado")
    logout_user()
    return redirect(url_for("login"))

@app.route("/cadastro", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        role = request.form.get("role","admin")
        if User.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado.", "error")
            return render_template("register.html")
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(name=name, email=email, password_hash=pw_hash, role=role)
        db.session.add(user)
        db.session.commit()
        flash("Conta criada com sucesso! Faça login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/convite/<token>", methods=["GET","POST"])
def accept_invite(token):
    invite = Invite.query.filter_by(token=token, used=False).first_or_404()
    company = Company.query.get(invite.company_id)
    if request.method == "POST":
        name = request.form.get("name","").strip()
        password = request.form.get("password","")
        if User.query.filter_by(email=invite.email).first():
            flash("E-mail já cadastrado. Faça login.", "error")
            return redirect(url_for("login"))
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(name=name, email=invite.email, password_hash=pw_hash, role="client", company_id=company.id)
        db.session.add(user)
        invite.used = True
        db.session.commit()
        login_user(user)
        log_activity("Cadastro via convite", company_id=company.id)
        flash(f"Bem-vindo(a)! Acesso ao inventário de {company.name} liberado.", "success")
        return redirect(url_for("client_dashboard"))
    return render_template("accept_invite.html", invite=invite, company=company)

# ─── ADMIN ───────────────────────────────────────────────────────────────────

@app.route("/admin")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        return redirect(url_for("client_dashboard"))
    companies = Company.query.order_by(Company.created_at.desc()).all()
    total_co2e = db.session.query(db.func.sum(EmissionEntry.total_co2e)).scalar() or 0
    recent_activity = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(10).all()
    return render_template("admin_dashboard.html",
        companies=companies, total_co2e=total_co2e,
        recent_activity=recent_activity, user_count=User.query.count(),
        today=date.today()
    )

@app.route("/admin/dashboard")
@login_required
def admin_dashboard_full():
    if current_user.role != "admin":
        return redirect(url_for("client_dashboard_full"))
    companies = Company.query.order_by(Company.name).all()
    company_id = request.args.get("company_id", type=int)
    scope = request.args.get("scope", type=int)
    category = request.args.get("category","")
    sel_year = request.args.get("year", type=int)
    q = EmissionEntry.query
    if company_id:
        q = q.filter_by(company_id=company_id)
    if scope:
        q = q.filter_by(scope=scope)
    if category:
        q = q.filter_by(category=category)
    if sel_year:
        q = q.filter_by(inventory_year=sel_year)
    emissions = q.order_by(EmissionEntry.inventory_year.desc(), EmissionEntry.created_at.desc()).all()
    scope_totals = {1:0.0, 2:0.0, 3:0.0}
    category_totals = {}
    company_totals = {}
    for e in emissions:
        scope_totals[e.scope] = scope_totals.get(e.scope,0) + e.total_co2e
        category_totals[e.category] = category_totals.get(e.category,0) + e.total_co2e
        cname = e.company.name if e.company else "—"
        company_totals[cname] = company_totals.get(cname,0) + e.total_co2e
    total = sum(scope_totals.values())
    all_categories = [r[0] for r in db.session.query(EmissionEntry.category).distinct().all()]
    all_years = [r[0] for r in db.session.query(EmissionEntry.inventory_year).distinct().order_by(EmissionEntry.inventory_year.desc()).all()]
    return render_template("admin_dashboard_full.html",
        companies=companies, emissions=emissions,
        scope_totals=scope_totals, category_totals=category_totals,
        company_totals=company_totals, total=total,
        all_categories=all_categories, all_years=all_years,
        sel_company=company_id, sel_scope=scope, sel_category=category, sel_year=sel_year,
        today=date.today()
    )

@app.route("/admin/clientes/novo", methods=["GET","POST"])
@login_required
def new_company():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    if request.method == "POST":
        company = Company(
            name=request.form["name"],
            cnpj=request.form.get("cnpj"),
            sector=request.form.get("sector"),
            responsible_id=current_user.id
        )
        db.session.add(company)
        db.session.commit()
        log_activity(f"Cliente cadastrado: {company.name}", company_id=company.id)
        flash(f"Cliente {company.name} cadastrado!", "success")
        return redirect(url_for("company_detail", company_id=company.id))
    return render_template("new_company.html")

@app.route("/admin/clientes/<int:company_id>")
@login_required
def company_detail(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    company = Company.query.get_or_404(company_id)
    sel_year = request.args.get("year", type=int)
    all_years = get_years_for_company(company_id)
    q = EmissionEntry.query.filter_by(company_id=company_id)
    if sel_year:
        q = q.filter_by(inventory_year=sel_year)
    emissions = q.all()
    users = User.query.filter_by(company_id=company_id).all()
    activities = ActivityLog.query.filter_by(company_id=company_id).order_by(ActivityLog.created_at.desc()).limit(20).all()
    invites = Invite.query.filter_by(company_id=company_id, used=False).all()
    scope_totals = {1:0.0, 2:0.0, 3:0.0}
    for e in emissions:
        scope_totals[e.scope] = scope_totals.get(e.scope,0) + e.total_co2e
    total = sum(scope_totals.values())
    return render_template("company_detail.html",
        company=company, emissions=emissions, users=users,
        activities=activities, invites=invites,
        scope_totals=scope_totals, total=total,
        all_years=all_years, sel_year=sel_year,
        base_url=request.host_url
    )

@app.route("/admin/clientes/<int:company_id>/convidar", methods=["POST"])
@login_required
def send_invite(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    email = request.form.get("email","").strip().lower()
    company = Company.query.get_or_404(company_id)
    token = secrets.token_urlsafe(32)
    invite = Invite(email=email, company_id=company_id, token=token)
    db.session.add(invite)
    db.session.commit()
    log_activity(f"Convite enviado para {email}", company_id=company_id)
    invite_url = url_for("accept_invite", token=token, _external=True)
    flash(f"Convite gerado! Link: {invite_url}", "success")
    return redirect(url_for("company_detail", company_id=company_id))

@app.route("/admin/lancamento", methods=["GET","POST"])
@login_required
def admin_new_entry():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    companies = Company.query.all()
    if request.method == "POST":
        company_id = int(request.form["company_id"])
        quantity = float(request.form["quantity"])
        ef = float(request.form["emission_factor"])
        total = round(quantity * ef / 1000, 6)
        entry = EmissionEntry(
            company_id=company_id,
            inventory_year=int(request.form.get("inventory_year", date.today().year)),
            scope=int(request.form["scope"]),
            category=request.form["category"],
            source_name=request.form["source_name"],
            fuel_type=request.form.get("fuel_type"),
            quantity=quantity, unit=request.form["unit"],
            emission_factor=ef,
            ef_source=request.form.get("ef_source","MCTI 2024"),
            total_co2e=total,
            period=request.form.get("period"),
            notes=request.form.get("notes"),
            created_by=current_user.id
        )
        db.session.add(entry)
        db.session.commit()
        log_activity(f"Emissão lançada: {entry.source_name} — {total} tCO₂e", company_id=company_id)
        flash(f"Lançamento salvo! Total: {total} tCO₂e", "success")
        return redirect(url_for("company_detail", company_id=company_id))
    presel = request.args.get("company", type=int)
    return render_template("new_entry.html", companies=companies,
        emission_factors=EMISSION_FACTORS, current_years=CURRENT_YEARS, presel=presel, today=date.today())

# ─── USUÁRIOS (ADMIN) ─────────────────────────────────────────────────────────

@app.route("/admin/usuarios")
@login_required
def admin_users():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    users = User.query.order_by(User.role, User.name).all()
    companies = Company.query.order_by(Company.name).all()
    return render_template("admin_users.html", users=users, companies=companies)

@app.route("/admin/usuarios/novo", methods=["POST"])
@login_required
def create_user():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    email = request.form.get("email","").strip().lower()
    if User.query.filter_by(email=email).first():
        flash("E-mail já cadastrado.", "error")
        return redirect(url_for("admin_users"))
    password = request.form.get("password","")
    pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
    company_id = request.form.get("company_id") or None
    role = request.form.get("role","client")
    scopes = request.form.getlist("scope_access")
    scope_str = "".join(sorted(scopes)) if scopes else "123"
    user = User(
        name=request.form.get("name","").strip(),
        email=email,
        password_hash=pw_hash,
        role=role,
        job_title=request.form.get("job_title","").strip(),
        phone=request.form.get("phone","").strip(),
        scope_access=scope_str,
        company_id=int(company_id) if company_id else None
    )
    db.session.add(user)
    db.session.commit()
    log_activity(f"Usuário criado: {user.name} ({role})")
    flash(f"Usuário {user.name} criado com sucesso!", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/usuarios/<int:user_id>/editar", methods=["POST"])
@login_required
def edit_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    user = User.query.get_or_404(user_id)
    user.name = request.form.get("name", user.name).strip()
    new_email = request.form.get("email", user.email).strip().lower()
    if new_email != user.email and User.query.filter_by(email=new_email).first():
        flash("E-mail já em uso.", "error")
        return redirect(url_for("admin_users"))
    user.email = new_email
    user.role = request.form.get("role", user.role)
    user.job_title = request.form.get("job_title","").strip()
    user.phone = request.form.get("phone","").strip()
    scopes = request.form.getlist("scope_access")
    if scopes:
        user.scope_access = "".join(sorted(scopes))
    company_id = request.form.get("company_id") or None
    user.company_id = int(company_id) if company_id else None
    new_pw = request.form.get("password","").strip()
    if new_pw:
        user.password_hash = bcrypt.generate_password_hash(new_pw).decode("utf-8")
    db.session.commit()
    log_activity(f"Usuário editado: {user.name}")
    flash(f"Usuário {user.name} atualizado!", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/usuarios/<int:user_id>/excluir", methods=["POST"])
@login_required
def delete_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    if user_id == current_user.id:
        flash("Você não pode excluir sua própria conta.", "error")
        return redirect(url_for("admin_users"))
    user = User.query.get_or_404(user_id)
    name = user.name
    db.session.delete(user)
    db.session.commit()
    log_activity(f"Usuário removido: {name}")
    flash(f"Usuário {name} removido.", "success")
    return redirect(url_for("admin_users"))

# ─── CLIENTE ─────────────────────────────────────────────────────────────────

@app.route("/cliente")
@login_required
def client_dashboard():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))
    if not current_user.company_id:
        flash("Conta não vinculada a nenhuma empresa.", "error")
        return redirect(url_for("login"))
    company = Company.query.get(current_user.company_id)
    all_years = get_years_for_company(company.id)
    latest_year = all_years[0] if all_years else date.today().year
    emissions = EmissionEntry.query.filter_by(company_id=company.id, inventory_year=latest_year).all()
    activities = ActivityLog.query.filter_by(company_id=company.id).order_by(ActivityLog.created_at.desc()).limit(10).all()
    scope_totals = {1:0.0, 2:0.0, 3:0.0}
    for e in emissions:
        scope_totals[e.scope] = scope_totals.get(e.scope,0) + e.total_co2e
    total = sum(scope_totals.values())
    return render_template("client_dashboard.html",
        company=company, emissions=emissions, activities=activities,
        scope_totals=scope_totals, total=total,
        all_years=all_years, sel_year=latest_year
    )

@app.route("/cliente/dashboard")
@login_required
def client_dashboard_full():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard_full"))
    company = Company.query.get(current_user.company_id)
    if not company:
        return redirect(url_for("login"))
    scope = request.args.get("scope", type=int)
    category = request.args.get("category","")
    sel_year = request.args.get("year", type=int)
    all_years = get_years_for_company(company.id)
    allowed = [int(s) for s in (current_user.scope_access or "123")]
    q = EmissionEntry.query.filter_by(company_id=company.id).filter(EmissionEntry.scope.in_(allowed))
    if scope and scope in allowed: q = q.filter_by(scope=scope)
    if category: q = q.filter_by(category=category)
    if sel_year: q = q.filter_by(inventory_year=sel_year)
    emissions = q.order_by(EmissionEntry.inventory_year.desc(), EmissionEntry.created_at.desc()).all()
    scope_totals = {1:0.0, 2:0.0, 3:0.0}
    category_totals = {}
    for e in emissions:
        scope_totals[e.scope] = scope_totals.get(e.scope,0) + e.total_co2e
        category_totals[e.category] = category_totals.get(e.category,0) + e.total_co2e
    total = sum(scope_totals.values())
    all_categories = [r[0] for r in db.session.query(EmissionEntry.category).filter(EmissionEntry.company_id==company.id).distinct().all()]
    return render_template("client_dashboard_full.html",
        company=company, emissions=emissions,
        scope_totals=scope_totals, category_totals=category_totals,
        total=total, all_categories=all_categories, all_years=all_years,
        sel_scope=scope, sel_category=category, sel_year=sel_year
    )

@app.route("/cliente/lancamento", methods=["GET","POST"])
@login_required
def client_new_entry():
    if current_user.role == "admin":
        return redirect(url_for("index"))
    company = Company.query.get(current_user.company_id)
    if not company:
        return redirect(url_for("login"))
    if request.method == "POST":
        quantity = float(request.form["quantity"])
        ef = float(request.form["emission_factor"])
        total = round(quantity * ef / 1000, 6)
        entry = EmissionEntry(
            company_id=company.id,
            inventory_year=int(request.form.get("inventory_year", date.today().year)),
            scope=int(request.form["scope"]),
            category=request.form["category"],
            source_name=request.form["source_name"],
            fuel_type=request.form.get("fuel_type"),
            quantity=quantity, unit=request.form["unit"],
            emission_factor=ef,
            ef_source=request.form.get("ef_source","MCTI 2024"),
            total_co2e=total,
            period=request.form.get("period"),
            notes=request.form.get("notes"),
            attachment_note=request.form.get("attachment_note"),
            created_by=current_user.id
        )
        db.session.add(entry)
        db.session.commit()
        log_activity(f"Emissão lançada pelo cliente: {entry.source_name} — {total} tCO₂e", company_id=company.id)
        flash(f"Lançamento salvo! Total calculado: {total} tCO₂e", "success")
        return redirect(url_for("client_dashboard_full"))
    now_month = date.today().strftime("%Y-%m")
    allowed_scopes = [int(s) for s in (current_user.scope_access or "123")]
    return render_template("client_new_entry.html", company=company,
        emission_factors=EMISSION_FACTORS, current_years=CURRENT_YEARS,
        now_month=now_month, allowed_scopes=allowed_scopes)

@app.route("/cliente/historico")
@login_required
def client_history():
    if current_user.role == "admin":
        return redirect(url_for("index"))
    company = Company.query.get(current_user.company_id)
    if not company:
        return redirect(url_for("login"))
    all_years = get_years_for_company(company.id)
    sel_years = request.args.getlist("years", type=int)
    if not sel_years and all_years:
        sel_years = [all_years[0]]
    allowed = [int(s) for s in (current_user.scope_access or "123")]
    q = EmissionEntry.query.filter_by(company_id=company.id).filter(EmissionEntry.scope.in_(allowed))
    if sel_years:
        q = q.filter(EmissionEntry.inventory_year.in_(sel_years))
    emissions = q.order_by(EmissionEntry.inventory_year.desc()).all()
    # Agrupar por ano
    by_year = {}
    for e in emissions:
        y = e.inventory_year
        if y not in by_year:
            by_year[y] = {1:0.0, 2:0.0, 3:0.0, "total":0.0, "entries":[]}
        by_year[y][e.scope] += e.total_co2e
        by_year[y]["total"] += e.total_co2e
        by_year[y]["entries"].append(e)
    return render_template("client_history.html",
        company=company, all_years=all_years,
        sel_years=sel_years, by_year=by_year
    )

# ─── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/emission-factors")
@login_required
def get_efs():
    return __import__("flask").jsonify(EMISSION_FACTORS)

# ─── SEED ─────────────────────────────────────────────────────────────────────

def seed_data():
    if User.query.count() > 0:
        return
    pw = bcrypt.generate_password_hash("admin123").decode("utf-8")
    admin = User(name="Ana Souza", email="admin@carbonmetric.com", password_hash=pw, role="admin", job_title="Consultora Sênior")
    db.session.add(admin)
    db.session.flush()
    company = Company(name="Petrobras S.A.", cnpj="33.000.167/0001-01", sector="Energia · Oil & Gas", responsible_id=admin.id)
    db.session.add(company)
    db.session.flush()
    pw_c = bcrypt.generate_password_hash("cliente123").decode("utf-8")
    client = User(name="Renata Figueiredo", email="cliente@carbonmetric.com", password_hash=pw_c, role="client", company_id=company.id)
    db.session.add(client)
    # Emissões demo para 2 anos
    demo = [
        (2024,1,"Combustão estacionária","Caldeira A","Gás natural",18400,"m³",2.02,"MCTI 2024",37.17,"2024-01"),
        (2024,1,"Combustão móvel","Frota veicular","Diesel",12800,"L",2.68,"MCTI 2024",34.30,"2024-01"),
        (2024,1,"Emissões fugitivas","Ar-cond. central","R-22",8.4,"kg",1810,"IPCC AR6",15.20,"2024-01"),
        (2024,2,"Eletricidade comprada (localização)","Sede SP","Energia elétrica SIN",142000,"kWh",0.0408,"MCTI 2024",57.94,"2024-01"),
        (2024,3,"Cat. 4 — Transporte upstream","Fornecedores logística","Transporte rodoviário",48000,"t·km",0.089,"SEEG v12",4.27,"2024-01"),
        (2023,1,"Combustão estacionária","Caldeira A","Gás natural",19200,"m³",2.02,"MCTI 2023",38.78,"2023-01"),
        (2023,2,"Eletricidade comprada (localização)","Sede SP","Energia elétrica SIN",138000,"kWh",0.0392,"MCTI 2023",54.10,"2023-01"),
    ]
    for row in demo:
        e = EmissionEntry(company_id=company.id, inventory_year=row[0], scope=row[1],
            category=row[2], source_name=row[3], fuel_type=row[4],
            quantity=row[5], unit=row[6], emission_factor=row[7],
            ef_source=row[8], total_co2e=row[9], period=row[10], created_by=admin.id)
        db.session.add(e)
    db.session.add(ActivityLog(company_id=company.id, user_id=admin.id, action="Inventário criado", detail="Dados demo 2023 e 2024"))
    db.session.commit()
    print("✅ Dados demo criados!")
    print("   Admin:   admin@carbonmetric.com / admin123")
    print("   Cliente: cliente@carbonmetric.com / cliente123")

with app.app_context():
    db.create_all()
    seed_data()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
