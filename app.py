from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from datetime import datetime, date
import secrets, string, os, io

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
    archived = db.Column(db.Boolean, default=False)
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
    gwp = db.Column(db.Float, nullable=True)
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

# Gases fugitivos com GWP — base MCTI 2024 (AR5, 100 anos)
FUGITIVE_GASES = [
    {"id":101,"name":"Dióxido de carbono (CO₂)","gwp":1,"familia":"-"},
    {"id":102,"name":"Metano (CH₄)","gwp":28,"familia":"-"},
    {"id":103,"name":"Óxido nitroso (N₂O)","gwp":265,"familia":"-"},
    {"id":104,"name":"HFC-23","gwp":12400,"familia":"HFC"},
    {"id":105,"name":"HFC-32","gwp":677,"familia":"HFC"},
    {"id":106,"name":"HFC-41","gwp":116,"familia":"HFC"},
    {"id":107,"name":"HFC-125","gwp":3170,"familia":"HFC"},
    {"id":108,"name":"HFC-134","gwp":1120,"familia":"HFC"},
    {"id":109,"name":"HFC-134a","gwp":1300,"familia":"HFC"},
    {"id":110,"name":"HFC-143","gwp":328,"familia":"HFC"},
    {"id":111,"name":"HFC-143a","gwp":4800,"familia":"HFC"},
    {"id":112,"name":"HFC-152","gwp":16,"familia":"HFC"},
    {"id":113,"name":"HFC-152a","gwp":138,"familia":"HFC"},
    {"id":114,"name":"HFC-161","gwp":4,"familia":"HFC"},
    {"id":115,"name":"HFC-227ea","gwp":3350,"familia":"HFC"},
    {"id":116,"name":"HFC-236cb","gwp":1210,"familia":"HFC"},
    {"id":117,"name":"HFC-236ea","gwp":1330,"familia":"HFC"},
    {"id":118,"name":"HFC-236fa","gwp":8060,"familia":"HFC"},
    {"id":119,"name":"HFC-245ca","gwp":716,"familia":"HFC"},
    {"id":120,"name":"HFC-245fa","gwp":858,"familia":"HFC"},
    {"id":121,"name":"HFC-365mfc","gwp":804,"familia":"HFC"},
    {"id":122,"name":"HFC-43-10mee","gwp":1650,"familia":"HFC"},
    {"id":123,"name":"Hexafluoreto de enxofre (SF₆)","gwp":23500,"familia":"-"},
    {"id":124,"name":"Trifluoreto de nitrogênio (NF₃)","gwp":16100,"familia":"-"},
    {"id":125,"name":"PFC-14","gwp":6630,"familia":"PFC"},
    {"id":126,"name":"PFC-116","gwp":11100,"familia":"PFC"},
    {"id":127,"name":"PFC-218","gwp":8900,"familia":"PFC"},
    {"id":128,"name":"PFC-318","gwp":9540,"familia":"PFC"},
    {"id":129,"name":"PFC-3-1-10","gwp":3200,"familia":"PFC"},
    {"id":130,"name":"PFC-4-1-12","gwp":8550,"familia":"PFC"},
    {"id":131,"name":"PFC-5-1-14","gwp":7910,"familia":"PFC"},
    {"id":132,"name":"PFC-9-1-18","gwp":7190,"familia":"PFC"},
    {"id":133,"name":"Trifluorometil pentafluoreto de enxofre","gwp":17400,"familia":"PFC"},
    {"id":134,"name":"Perfluorociclopropano","gwp":9200,"familia":"PFC"},
    # Misturas comuns de refrigerantes
    {"id":150,"name":"R-22 (HCFC-22)","gwp":1810,"familia":"HCFC"},
    {"id":151,"name":"R-410A (HFC-32/125)","gwp":2088,"familia":"HFC"},
    {"id":152,"name":"R-404A","gwp":3922,"familia":"HFC"},
    {"id":153,"name":"R-407C","gwp":1774,"familia":"HFC"},
    {"id":154,"name":"R-507A","gwp":3985,"familia":"HFC"},
    {"id":155,"name":"R-134a (HFC-134a)","gwp":1300,"familia":"HFC"},
]

EMISSION_FACTORS = [
    # ── ESCOPO 1 · COMBUSTÃO ESTACIONÁRIA ─────────────────────────────────────
    {"id":1,  "name":"Gás natural",              "scope":1,"category":"Combustão estacionária","fe":2.02,  "unit":"m³",  "source":"MCTI 2024","gwp":None},
    {"id":4,  "name":"GLP",                      "scope":1,"category":"Combustão estacionária","fe":3.01,  "unit":"kg",  "source":"MCTI 2024","gwp":None},
    {"id":6,  "name":"Carvão mineral",            "scope":1,"category":"Combustão estacionária","fe":2.54,  "unit":"kg",  "source":"SEEG v12", "gwp":None},
    {"id":13, "name":"Óleo combustível (BPF)",    "scope":1,"category":"Combustão estacionária","fe":3.11,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":14, "name":"Querosene iluminante",      "scope":1,"category":"Combustão estacionária","fe":2.52,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":15, "name":"Lenha / biomassa",          "scope":1,"category":"Combustão estacionária","fe":0.0,   "unit":"kg",  "source":"MCTI 2024","gwp":None},
    {"id":16, "name":"Bagaço de cana",            "scope":1,"category":"Combustão estacionária","fe":0.0,   "unit":"kg",  "source":"MCTI 2024","gwp":None},
    {"id":17, "name":"Coque de petróleo",         "scope":1,"category":"Combustão estacionária","fe":3.36,  "unit":"kg",  "source":"MCTI 2024","gwp":None},
    {"id":18, "name":"Óleo diesel (industrial)",  "scope":1,"category":"Combustão estacionária","fe":2.68,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    # ── ESCOPO 1 · COMBUSTÃO MÓVEL ────────────────────────────────────────────
    {"id":2,  "name":"Diesel (veículos)",         "scope":1,"category":"Combustão móvel","fe":2.68,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":3,  "name":"Gasolina (veículos)",       "scope":1,"category":"Combustão móvel","fe":2.27,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":5,  "name":"Etanol hidratado",          "scope":1,"category":"Combustão móvel","fe":1.46,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":19, "name":"Etanol anidro",             "scope":1,"category":"Combustão móvel","fe":1.46,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":20, "name":"GNV (gás natural veicular)","scope":1,"category":"Combustão móvel","fe":2.02,  "unit":"m³",  "source":"MCTI 2024","gwp":None},
    {"id":21, "name":"Querosene de aviação (QAV)","scope":1,"category":"Combustão móvel","fe":2.52,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":22, "name":"Gasolina de aviação",       "scope":1,"category":"Combustão móvel","fe":2.27,  "unit":"L",   "source":"MCTI 2024","gwp":None},
    {"id":23, "name":"Biodiesel B100",            "scope":1,"category":"Combustão móvel","fe":0.0,   "unit":"L",   "source":"MCTI 2024","gwp":None},
    # ── ESCOPO 1 · EMISSÕES FUGITIVAS (via seletor de gases) ──────────────────
    {"id":30, "name":"(selecione o gás abaixo)","scope":1,"category":"Emissões fugitivas","fe":0.0,"unit":"kg","source":"MCTI 2024 / IPCC AR5","gwp":None},
    # ── ESCOPO 1 · PROCESSO INDUSTRIAL ────────────────────────────────────────
    {"id":40, "name":"Calcário (CaCO₃)",         "scope":1,"category":"Processo industrial","fe":0.44,  "unit":"kg",  "source":"IPCC AR6", "gwp":None},
    {"id":41, "name":"Dolomita",                 "scope":1,"category":"Processo industrial","fe":0.47,  "unit":"kg",  "source":"IPCC AR6", "gwp":None},
    {"id":42, "name":"Soda cáustica",            "scope":1,"category":"Processo industrial","fe":0.57,  "unit":"kg",  "source":"IPCC AR6", "gwp":None},
    # ── ESCOPO 2 · ENERGIA ELÉTRICA ───────────────────────────────────────────
    {"id":9,  "name":"Energia elétrica SIN (localização)","scope":2,"category":"Eletricidade comprada (localização)","fe":0.0408,"unit":"kWh","source":"MCTI 2024","gwp":None},
    {"id":50, "name":"Energia elétrica SIN (mercado / REC)","scope":2,"category":"Eletricidade comprada (mercado)","fe":0.0,"unit":"kWh","source":"MCTI 2024","gwp":None},
    {"id":51, "name":"Energia elétrica — geração própria (solar/eólica)","scope":2,"category":"Eletricidade comprada (localização)","fe":0.0,"unit":"kWh","source":"MCTI 2024","gwp":None},
    {"id":52, "name":"Vapor comprado",           "scope":2,"category":"Calor/vapor comprado","fe":0.27,  "unit":"GJ",  "source":"IPCC AR6", "gwp":None},
    # ── ESCOPO 3 · CATEGORIA 1 — BENS E SERVIÇOS COMPRADOS ───────────────────
    {"id":60, "name":"Compras gerais (spend-based)","scope":3,"category":"Cat. 1 — Bens e serviços comprados","fe":0.31,"unit":"R$1000","source":"SEEG v12","gwp":None},
    {"id":61, "name":"Insumos industriais",      "scope":3,"category":"Cat. 1 — Bens e serviços comprados","fe":0.45,"unit":"kg","source":"SEEG v12","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 2 — BENS DE CAPITAL ─────────────────────────────
    {"id":62, "name":"Bens de capital (spend-based)","scope":3,"category":"Cat. 2 — Bens de capital","fe":0.31,"unit":"R$1000","source":"SEEG v12","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 3 — ATIVIDADES DE ENERGIA ───────────────────────
    {"id":63, "name":"Perdas na transmissão elétrica","scope":3,"category":"Cat. 3 — Atividades de energia","fe":0.0071,"unit":"kWh","source":"MCTI 2024","gwp":None},
    {"id":64, "name":"Extração e transporte de gás natural","scope":3,"category":"Cat. 3 — Atividades de energia","fe":0.34,"unit":"m³","source":"IPCC AR6","gwp":None},
    {"id":65, "name":"Extração e refino de diesel","scope":3,"category":"Cat. 3 — Atividades de energia","fe":0.63,"unit":"L","source":"IPCC AR6","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 4 — TRANSPORTE UPSTREAM ─────────────────────────
    {"id":10, "name":"Transporte rodoviário de carga","scope":3,"category":"Cat. 4 — Transporte upstream","fe":0.089,"unit":"t·km","source":"SEEG v12","gwp":None},
    {"id":70, "name":"Transporte ferroviário de carga","scope":3,"category":"Cat. 4 — Transporte upstream","fe":0.028,"unit":"t·km","source":"IPCC AR6","gwp":None},
    {"id":71, "name":"Transporte marítimo de carga","scope":3,"category":"Cat. 4 — Transporte upstream","fe":0.011,"unit":"t·km","source":"IPCC AR6","gwp":None},
    {"id":72, "name":"Transporte aéreo de carga","scope":3,"category":"Cat. 4 — Transporte upstream","fe":1.13, "unit":"t·km","source":"IPCC AR6","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 5 — RESÍDUOS ────────────────────────────────────
    {"id":12, "name":"Resíduos sólidos — aterro sanitário","scope":3,"category":"Cat. 5 — Resíduos gerados","fe":0.52, "unit":"kg","source":"MCTI 2024","gwp":None},
    {"id":80, "name":"Resíduos sólidos — incineração","scope":3,"category":"Cat. 5 — Resíduos gerados","fe":2.09, "unit":"kg","source":"MCTI 2024","gwp":None},
    {"id":81, "name":"Resíduos sólidos — compostagem","scope":3,"category":"Cat. 5 — Resíduos gerados","fe":0.015,"unit":"kg","source":"IPCC AR6","gwp":None},
    {"id":82, "name":"Efluentes líquidos (tratamento anaeróbio)","scope":3,"category":"Cat. 5 — Resíduos gerados","fe":0.48, "unit":"m³","source":"MCTI 2024","gwp":None},
    {"id":83, "name":"Efluentes líquidos (tratamento aeróbio)","scope":3,"category":"Cat. 5 — Resíduos gerados","fe":0.12, "unit":"m³","source":"MCTI 2024","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 6 — VIAGENS A NEGÓCIOS ──────────────────────────
    {"id":11, "name":"Voo doméstico (econômica)","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.158,"unit":"pkm","source":"IPCC AR6","gwp":None},
    {"id":90, "name":"Voo internacional (econômica)","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.195,"unit":"pkm","source":"IPCC AR6","gwp":None},
    {"id":91, "name":"Voo internacional (executiva)","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.429,"unit":"pkm","source":"IPCC AR6","gwp":None},
    {"id":92, "name":"Carro alugado — gasolina","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.171,"unit":"km", "source":"IPCC AR6","gwp":None},
    {"id":93, "name":"Ônibus interestadual","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":0.089,"unit":"pkm","source":"SEEG v12","gwp":None},
    {"id":94, "name":"Hospedagem (hotel, por noite)","scope":3,"category":"Cat. 6 — Viagens a negócios","fe":31.0, "unit":"un","source":"IPCC AR6","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 7 — DESLOCAMENTO DE FUNCIONÁRIOS ────────────────
    {"id":100,"name":"Carro próprio — gasolina (casa-trabalho)","scope":3,"category":"Cat. 7 — Deslocamento de funcionários","fe":0.171,"unit":"km","source":"IPCC AR6","gwp":None},
    {"id":101,"name":"Carro próprio — etanol (casa-trabalho)","scope":3,"category":"Cat. 7 — Deslocamento de funcionários","fe":0.055,"unit":"km","source":"MCTI 2024","gwp":None},
    {"id":102,"name":"Ônibus urbano (casa-trabalho)","scope":3,"category":"Cat. 7 — Deslocamento de funcionários","fe":0.089,"unit":"pkm","source":"SEEG v12","gwp":None},
    {"id":103,"name":"Metrô / trem urbano (casa-trabalho)","scope":3,"category":"Cat. 7 — Deslocamento de funcionários","fe":0.041,"unit":"pkm","source":"SEEG v12","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 11 — USO DE PRODUTOS VENDIDOS ───────────────────
    {"id":110,"name":"Uso de produtos vendidos (spend-based)","scope":3,"category":"Cat. 11 — Uso de produtos vendidos","fe":0.31,"unit":"R$1000","source":"SEEG v12","gwp":None},
    # ── ESCOPO 3 · CATEGORIA 12 — FIM DE VIDA ────────────────────────────────
    {"id":120,"name":"Fim de vida — aterro","scope":3,"category":"Cat. 12 — Fim de vida dos produtos","fe":0.52,"unit":"kg","source":"MCTI 2024","gwp":None},
    {"id":121,"name":"Fim de vida — reciclagem","scope":3,"category":"Cat. 12 — Fim de vida dos produtos","fe":0.02,"unit":"kg","source":"IPCC AR6","gwp":None},
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
    show_archived = request.args.get("archived") == "1"
    companies = Company.query.filter_by(archived=show_archived).order_by(Company.created_at.desc()).all()
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
    companies = Company.query.filter_by(archived=False).order_by(Company.name).all()
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
        gwp_val = request.form.get("gwp")
        entry = EmissionEntry(
            company_id=company_id,
            inventory_year=int(request.form.get("inventory_year", date.today().year)),
            scope=int(request.form["scope"]),
            category=request.form["category"],
            source_name=request.form["source_name"],
            fuel_type=request.form.get("fuel_type"),
            quantity=quantity, unit=request.form["unit"],
            emission_factor=ef,
            gwp=float(gwp_val) if gwp_val else None,
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
        emission_factors=EMISSION_FACTORS, fugitive_gases=FUGITIVE_GASES,
        current_years=CURRENT_YEARS, presel=presel, today=date.today())

# ─── USUÁRIOS (ADMIN) ─────────────────────────────────────────────────────────

@app.route("/admin/usuarios")
@login_required
def admin_users():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    users = User.query.order_by(User.role, User.name).all()
    companies = Company.query.filter_by(archived=False).order_by(Company.name).all()
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
        gwp_val = request.form.get("gwp")
        entry = EmissionEntry(
            company_id=company.id,
            inventory_year=int(request.form.get("inventory_year", date.today().year)),
            scope=int(request.form["scope"]),
            category=request.form["category"],
            source_name=request.form["source_name"],
            fuel_type=request.form.get("fuel_type"),
            quantity=quantity, unit=request.form["unit"],
            emission_factor=ef,
            gwp=float(gwp_val) if gwp_val else None,
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
        emission_factors=EMISSION_FACTORS, fugitive_gases=FUGITIVE_GASES,
        current_years=CURRENT_YEARS,
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


# ─── EXCLUIR LANÇAMENTO ──────────────────────────────────────────────────────

@app.route("/admin/lancamento/<int:entry_id>/excluir", methods=["POST"])
@login_required
def delete_entry(entry_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    entry = EmissionEntry.query.get_or_404(entry_id)
    company_id = entry.company_id
    name = entry.source_name
    db.session.delete(entry)
    db.session.commit()
    log_activity(f"Lançamento excluído: {name}", company_id=company_id)
    flash(f"Lançamento '{name}' excluído com sucesso.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))

@app.route("/cliente/lancamento/<int:entry_id>/excluir", methods=["POST"])
@login_required
def client_delete_entry(entry_id):
    entry = EmissionEntry.query.get_or_404(entry_id)
    if entry.company_id != current_user.company_id:
        flash("Acesso negado.", "error")
        return redirect(url_for("client_dashboard_full"))
    name = entry.source_name
    db.session.delete(entry)
    db.session.commit()
    log_activity(f"Lançamento excluído pelo cliente: {name}", company_id=current_user.company_id)
    flash(f"Lançamento '{name}' excluído.", "success")
    return redirect(url_for("client_dashboard_full"))

# ─── ARQUIVAR / EXCLUIR CLIENTE ───────────────────────────────────────────────

@app.route("/admin/clientes/<int:company_id>/arquivar", methods=["POST"])
@login_required
def archive_company(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    company = Company.query.get_or_404(company_id)
    company.archived = True
    db.session.commit()
    log_activity(f"Cliente arquivado: {company.name}", company_id=company_id)
    flash(f"Cliente '{company.name}' arquivado. Não aparecerá nos dashboards.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/clientes/<int:company_id>/reativar", methods=["POST"])
@login_required
def unarchive_company(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    company = Company.query.get_or_404(company_id)
    company.archived = False
    db.session.commit()
    log_activity(f"Cliente reativado: {company.name}", company_id=company_id)
    flash(f"Cliente '{company.name}' reativado.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/clientes/<int:company_id>/excluir", methods=["POST"])
@login_required
def delete_company(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    company = Company.query.get_or_404(company_id)
    name = company.name
    # Desvincular usuários
    User.query.filter_by(company_id=company_id).update({"company_id": None})
    db.session.delete(company)
    db.session.commit()
    log_activity(f"Cliente excluído permanentemente: {name}")
    flash(f"Cliente '{name}' excluído permanentemente.", "success")
    return redirect(url_for("admin_dashboard"))

# ─── API GASES FUGITIVOS ─────────────────────────────────────────────────────

@app.route("/api/fugitive-gases")
@login_required
def get_fugitive_gases():
    return __import__("flask").jsonify(FUGITIVE_GASES)


# ─── UPLOAD PLANILHA GHG PROTOCOL ────────────────────────────────────────────

def parse_ghg_spreadsheet(file_bytes, company_id, inventory_year, created_by_id):
    """
    Lê a Ferramenta GHG Protocol (.xlsx) e retorna lista de EmissionEntry.
    Abas processadas: Combustão estacionária, Combustão móvel,
    Emissões fugitivas, En. elétrica (localização),
    Emissões casa-trabalho.
    """
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    entries = []
    errors = []

    def safe_float(v):
        try:
            f = float(str(v).replace(",", "."))
            return f if f > 0 else None
        except Exception:
            return None

    def make_entry(scope, category, source_name, fuel_type, quantity, unit, ef, ef_source, gwp=None):
        if quantity and quantity > 0 and ef and ef > 0:
            total = round(quantity * ef / 1000, 6)
            return EmissionEntry(
                company_id=company_id,
                inventory_year=inventory_year,
                scope=scope,
                category=category,
                source_name=source_name,
                fuel_type=fuel_type,
                quantity=quantity,
                unit=unit,
                emission_factor=ef,
                ef_source=ef_source,
                gwp=gwp,
                total_co2e=total,
                period=str(inventory_year) + "-01",
                notes="Importado via planilha GHG Protocol",
                created_by=created_by_id
            )
        return None

    # Mapa de combustíveis para FE (kgCO2e/unidade)
    FUEL_FE_MAP = {
        "gasolina automotiva": (2.27, "L", "MCTI 2024"),
        "gasolina": (2.27, "L", "MCTI 2024"),
        "óleo diesel": (2.68, "L", "MCTI 2024"),
        "diesel": (2.68, "L", "MCTI 2024"),
        "etanol hidratado": (1.46, "L", "MCTI 2024"),
        "etanol anidro": (1.46, "L", "MCTI 2024"),
        "gás natural": (2.02, "m³", "MCTI 2024"),
        "glp": (3.01, "kg", "MCTI 2024"),
        "carvão metalúrgico nacional": (2543.0, "Toneladas", "MCTI 2024"),
        "carvão metalúrgico importado": (2931.0, "Toneladas", "MCTI 2024"),
        "querosene": (2.52, "L", "MCTI 2024"),
        "biodiesel": (0.0, "L", "MCTI 2024"),
    }

    def get_fe(fuel_name):
        if not fuel_name:
            return None, None, None
        key = str(fuel_name).lower().strip()
        for k, v in FUEL_FE_MAP.items():
            if k in key:
                return v
        return None, None, None

    # GWP map para fugitivas
    GWP_MAP = {
        "hfc-32": 677, "hfc-125": 3170, "hfc-134a": 1300, "hfc-410a": 2088,
        "r-410a": 2088, "r-22": 1810, "hfc-23": 12400, "sf6": 23500,
        "co2": 1, "dióxido de carbono": 1, "ch4": 28, "metano": 28,
        "n2o": 265, "óxido nitroso": 265, "hfc-227ea": 3350, "hfc-143a": 4800,
    }
    def get_gwp(gas_name):
        if not gas_name: return 1
        key = str(gas_name).lower().strip()
        for k, v in GWP_MAP.items():
            if k in key: return v
        return 1

    # ── 1. COMBUSTÃO ESTACIONÁRIA ──────────────────────────────────────────
    if "Combustão estacionária" in wb.sheetnames:
        ws = wb["Combustão estacionária"]
        for i, row in enumerate(ws.iter_rows(min_row=47, max_row=146, values_only=True), 47):
            r = list(row)
            if len(r) < 10: continue
            source = r[1]  # Registro da fonte
            desc   = r[2]  # Descrição
            fuel   = r[3]  # Combustível
            qty    = safe_float(r[4])   # Quantidade consumida
            unit   = r[5] or "un"      # Unidades
            if not source or not qty: continue
            fe, fe_unit, fe_src = get_fe(fuel)
            if fe is None:
                errors.append(f"Combustão estacionária L{i}: FE não encontrado para '{fuel}'")
                fe, fe_src = 2.02, "MCTI 2024 (estimado)"
            e = make_entry(1, "Combustão estacionária",
                           f"{source} — {desc}" if desc else str(source),
                           str(fuel) if fuel else "Combustível",
                           qty, str(unit), fe, fe_src)
            if e: entries.append(e)

    # ── 2. COMBUSTÃO MÓVEL ────────────────────────────────────────────────
    # Usa lançamentos individuais por veículo E também verifica o total (col 54)
    if "Combustão móvel" in wb.sheetnames:
        ws = wb["Combustão móvel"]
        movel_entries = []
        for i, row in enumerate(ws.iter_rows(min_row=48, max_row=360, values_only=True), 48):
            r = list(row)
            if len(r) < 20: continue
            if str(r[1] or "").strip() in ["Total", "Registro da frota", ""]: continue
            source = r[1]
            desc   = r[2]
            vtype  = r[3]
            qty    = safe_float(r[17])
            unit   = r[18] or "L"
            fuel   = r[19]
            if not source or not qty: continue
            fe, fe_unit, fe_src = get_fe(fuel)
            if fe is None:
                if "diesel" in str(vtype).lower(): fe, fe_src = 2.68, "MCTI 2024"
                elif "flex" in str(vtype).lower() or "gasolina" in str(vtype).lower(): fe, fe_src = 2.27, "MCTI 2024"
                else: fe, fe_src = 2.27, "MCTI 2024 (estimado)"
            e = make_entry(1, "Combustão móvel",
                           f"{source} — {desc}" if desc else str(source),
                           str(fuel) if fuel else str(vtype),
                           qty, str(unit), fe, fe_src)
            if e: movel_entries.append(e)
        # Verificar se o total dos lançamentos bate com o total da planilha (L199 col 54)
        if movel_entries:
            sum_movel = sum(e.total_co2e for e in movel_entries)
            # Buscar total oficial
            for i, row in enumerate(ws.iter_rows(min_row=197, max_row=202, values_only=True), 197):
                r = list(row)
                if len(r) > 54 and str(r[1] or "").strip() == "Total" and r[54]:
                    official_total = safe_float(r[54])
                    if official_total and abs(official_total - sum_movel) > 0.1:
                        # Diferença > 0.1 tCO2e: adicionar lançamento de ajuste
                        diff = official_total - sum_movel
                        adj = EmissionEntry(
                            company_id=company_id, inventory_year=inventory_year,
                            scope=1, category="Combustão móvel",
                            source_name="Ajuste — diferença de arredondamento",
                            fuel_type="Ajuste automático (diferença planilha vs lançamentos)",
                            quantity=abs(diff * 1000), unit="kg CO2e",
                            emission_factor=1.0, ef_source="GHG Protocol",
                            gwp=None, total_co2e=round(diff, 6),
                            period=str(inventory_year) + "-01",
                            notes="Ajuste automático para alinhar ao total oficial da planilha",
                            created_by=created_by_id
                        )
                        movel_entries.append(adj)
                    break
            entries.extend(movel_entries)

    # ── 3. EMISSÕES FUGITIVAS ─────────────────────────────────────────────
    # Usa o total da Tabela 2 (balanço de massa) — linha 167, coluna H (índice 7)
    # Este é o valor mais preciso, já calculado pela ferramenta GHG Protocol
    if "Emissões fugitivas" in wb.sheetnames:
        ws = wb["Emissões fugitivas"]
        # Primeiro tenta a Tabela 2 (balanço de massa) — total em H167
        fug_total = None
        for i, row in enumerate(ws.iter_rows(min_row=160, max_row=175, values_only=True), 160):
            r = list(row)
            if len(r) > 1 and str(r[1]).strip() == "Total" and len(r) > 7 and r[7]:
                fug_val = safe_float(r[7])
                if fug_val and fug_val > 0:
                    fug_total = fug_val
                    break
        # Fallback: Tabela 1 (simples) — total em J112
        if not fug_total:
            for i, row in enumerate(ws.iter_rows(min_row=108, max_row=116, values_only=True), 108):
                r = list(row)
                if len(r) > 1 and str(r[1]).strip() == "Total":
                    for col_idx in [9, 7, 8, 10]:
                        if len(r) > col_idx and safe_float(r[col_idx]):
                            fug_total = safe_float(r[col_idx])
                            break
                    if fug_total:
                        break
        if fug_total and fug_total > 0:
            # Criar lançamento consolidado com o total correto
            entry = EmissionEntry(
                company_id=company_id,
                inventory_year=inventory_year,
                scope=1,
                category="Emissões fugitivas",
                source_name="Emissões fugitivas — total consolidado",
                fuel_type="Gases refrigerantes / GEE (R-410A, CO₂, HFCs)",
                quantity=fug_total * 1000,  # tCO2e → kg equivalente
                unit="kg CO2e",
                emission_factor=1.0,
                ef_source="GHG Protocol — balanço de massa (H167)",
                gwp=None,
                total_co2e=round(fug_total, 6),
                period=str(inventory_year) + "-01",
                notes="Total importado da Ferramenta GHG Protocol — Tabela 2 (balanço de massa com variação de estoque)",
                created_by=created_by_id
            )
            entries.append(entry)

        # ── 4. ENERGIA ELÉTRICA ───────────────────────────────────────────────
    # Usa lançamentos individuais por unidade E valida com o total (L91 col 29)
    if "En. elétrica (localização)" in wb.sheetnames:
        ws = wb["En. elétrica (localização)"]
        eletrica_entries = []
        for i, row in enumerate(ws.iter_rows(min_row=41, max_row=90, values_only=True), 41):
            r = list(row)
            if len(r) < 17: continue
            if str(r[1] or "").strip() in ["Total", "Registro da fonte", ""]: continue
            source = r[1]
            desc   = r[2]
            qty    = safe_float(r[16])  # Total anual MWh
            if not source or not qty: continue
            e = make_entry(2, "Eletricidade comprada (localização)",
                           f"{source} — {desc}" if desc else str(source),
                           "Energia elétrica SIN",
                           qty * 1000, "kWh", 0.0408, "MCTI 2024")
            if e: eletrica_entries.append(e)
        # Validar com total oficial (L91, col 29 = tCO2 total)
        for i, row in enumerate(ws.iter_rows(min_row=89, max_row=93, values_only=True), 89):
            r = list(row)
            if str(r[1] or "").strip() == "Total" and len(r) > 29 and r[29]:
                official = safe_float(r[29])
                if official and official > 0 and not eletrica_entries:
                    # Não encontrou lançamentos individuais — usa total consolidado
                    entry = EmissionEntry(
                        company_id=company_id, inventory_year=inventory_year,
                        scope=2, category="Eletricidade comprada (localização)",
                        source_name="Energia elétrica — total consolidado",
                        fuel_type="Energia elétrica SIN",
                        quantity=official / 0.0408 * 1000, unit="kWh",
                        emission_factor=0.0408, ef_source="MCTI 2024",
                        gwp=None, total_co2e=round(official, 6),
                        period=str(inventory_year) + "-01",
                        notes="Total importado da Ferramenta GHG Protocol",
                        created_by=created_by_id
                    )
                    eletrica_entries.append(entry)
                break
        entries.extend(eletrica_entries)

    # ── 5. CASA-TRABALHO ──────────────────────────────────────────────────
    if "Emissões casa-trabalho" in wb.sheetnames:
        ws = wb["Emissões casa-trabalho"]
        for i, row in enumerate(ws.iter_rows(min_row=40, max_row=200, values_only=True), 40):
            r = list(row)
            if len(r) < 11: continue
            source = r[1]  # Colaborador / registro
            desc   = r[2]  # Percurso
            ttype  = r[3]  # Tipo de transporte
            pax    = safe_float(r[4]) or 1
            dist   = safe_float(r[5])  # Distância km
            days   = safe_float(r[6]) or 230
            fe_g   = safe_float(r[7])  # gCO2/P.km
            if not source or not dist or not fe_g: continue
            pkm    = dist * days * pax  # total pkm/ano
            fe_kg  = fe_g / 1000        # g → kg
            e = make_entry(3, "Cat. 7 — Deslocamento de funcionários",
                           f"{source} — {desc}" if desc else str(source),
                           str(ttype) if ttype else "Transporte",
                           pkm, "pkm", fe_kg, "GHG Protocol")
            if e: entries.append(e)

    return entries, errors


@app.route("/admin/clientes/<int:company_id>/upload-planilha", methods=["GET", "POST"])
@login_required
def upload_ghg_spreadsheet(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    company = Company.query.get_or_404(company_id)
    if request.method == "POST":
        f = request.files.get("file")
        inventory_year = int(request.form.get("inventory_year", date.today().year))
        if not f or not f.filename.endswith((".xlsx", ".xlsm")):
            flash("Envie um arquivo .xlsx válido.", "error")
            return redirect(request.url)
        file_bytes = f.read()
        entries, errors = parse_ghg_spreadsheet(file_bytes, company_id, inventory_year, current_user.id)
        if not entries:
            flash("Nenhum dado encontrado na planilha. Verifique se está preenchida.", "error")
            return redirect(request.url)
        for e in entries:
            db.session.add(e)
        db.session.commit()
        log_activity(
            f"Planilha GHG Protocol importada: {len(entries)} lançamentos",
            detail=f"Arquivo: {f.filename} | Erros: {len(errors)}",
            company_id=company_id
        )
        msg = f"Importação concluída! {len(entries)} lançamentos criados."
        if errors:
            msg += f" ({len(errors)} avisos — alguns FEs foram estimados)."
        flash(msg, "success")
        return redirect(url_for("company_detail", company_id=company_id))
    return render_template("upload_spreadsheet.html",
        company=company, current_years=CURRENT_YEARS, now_year=date.today().year)


@app.route("/cliente/upload-planilha", methods=["GET", "POST"])
@login_required
def client_upload_spreadsheet():
    if current_user.role == "admin":
        return redirect(url_for("index"))
    company = Company.query.get(current_user.company_id)
    if not company:
        return redirect(url_for("login"))
    if request.method == "POST":
        f = request.files.get("file")
        inventory_year = int(request.form.get("inventory_year", date.today().year))
        if not f or not f.filename.endswith((".xlsx", ".xlsm")):
            flash("Envie um arquivo .xlsx válido.", "error")
            return redirect(request.url)
        file_bytes = f.read()
        entries, errors = parse_ghg_spreadsheet(file_bytes, company.id, inventory_year, current_user.id)
        if not entries:
            flash("Nenhum dado encontrado na planilha. Verifique se está preenchida.", "error")
            return redirect(request.url)
        for e in entries:
            db.session.add(e)
        db.session.commit()
        log_activity(
            f"Planilha GHG Protocol importada pelo cliente: {len(entries)} lançamentos",
            company_id=company.id
        )
        msg = f"Importação concluída! {len(entries)} lançamentos criados."
        if errors:
            msg += f" ({len(errors)} avisos de FEs estimados)."
        flash(msg, "success")
        return redirect(url_for("client_dashboard_full"))
    return render_template("upload_spreadsheet.html",
        company=company, current_years=CURRENT_YEARS, now_year=date.today().year)

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

def migrate_db():
    default_123 = "123"
    with db.engine.connect() as conn:
        for table, col, col_type in [
            ("company", "archived", "BOOLEAN DEFAULT FALSE"),
            ("emission_entry", "gwp", "FLOAT"),
        ]:
            try:
                conn.execute(db.text("ALTER TABLE " + table + " ADD COLUMN " + col + " " + col_type))
                conn.commit()
            except Exception:
                pass
        for sql in [
            'ALTER TABLE "user" ADD COLUMN scope_access VARCHAR(10) DEFAULT ' + "'" + default_123 + "'",
            "ALTER TABLE user ADD COLUMN scope_access VARCHAR(10) DEFAULT '" + default_123 + "'",
        ]:
            try:
                conn.execute(db.text(sql))
                conn.commit()
                break
            except Exception:
                pass

with app.app_context():
    db.create_all()
    migrate_db()
    seed_data()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

# ─── EXPORTAR PDF DO INVENTÁRIO ───────────────────────────────────────────────

@app.route("/admin/clientes/<int:company_id>/exportar-pdf")
@login_required
def export_company_pdf(company_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, HRFlowable, PageBreak, KeepTogether)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics import renderPDF
    import io as _io
    from datetime import datetime as dt

    company = Company.query.get_or_404(company_id)
    inv_year = request.args.get("year", type=int)

    # Buscar lançamentos
    q = EmissionEntry.query.filter_by(company_id=company_id)
    if inv_year:
        q = q.filter_by(inventory_year=inv_year)
    else:
        # Usar o ano mais recente
        latest = db.session.query(db.func.max(EmissionEntry.inventory_year)).filter_by(company_id=company_id).scalar()
        inv_year = latest or dt.now().year
        q = q.filter_by(inventory_year=inv_year)
    emissions = q.all()

    # Totais por escopo e categoria
    e1_mobile = sum(e.total_co2e for e in emissions if e.scope == 1 and "móvel" in (e.category or "").lower())
    e1_fugitive = sum(e.total_co2e for e in emissions if e.scope == 1 and "fugit" in (e.category or "").lower())
    e1_stationary = sum(e.total_co2e for e in emissions if e.scope == 1 and "estacion" in (e.category or "").lower())
    e1_process = sum(e.total_co2e for e in emissions if e.scope == 1 and "processo" in (e.category or "").lower())
    e1_other = sum(e.total_co2e for e in emissions if e.scope == 1
                   and not any(k in (e.category or "").lower() for k in ["móvel","fugit","estacion","processo"]))
    e1_total = sum(e.total_co2e for e in emissions if e.scope == 1)
    e2_total = sum(e.total_co2e for e in emissions if e.scope == 2)
    e3_total = sum(e.total_co2e for e in emissions if e.scope == 3)
    grand_total = e1_total + e2_total + e3_total

    # Responsável técnico (admin logado)
    admin_user = User.query.filter_by(role="admin").first()
    resp_name = admin_user.name if admin_user else "Interbio Tecnologia Ambiental"
    resp_job = admin_user.job_title if (admin_user and admin_user.job_title) else "Responsável Técnico"

    # ── CORES E ESTILOS ──────────────────────────────────────────────────
    TEAL       = colors.HexColor("#1B5E6B")
    TEAL_LIGHT = colors.HexColor("#2DBD8F")
    GREEN      = colors.HexColor("#2DBD8F")
    GRAY       = colors.HexColor("#5a5a57")
    LIGHT_GRAY = colors.HexColor("#f5f5f3")
    WHITE      = colors.white
    BLACK      = colors.HexColor("#1a1a18")

    styles = getSampleStyleSheet()

    def sty(name, **kwargs):
        base = styles["Normal"]
        return ParagraphStyle(name, parent=base, **kwargs)

    s_title      = sty("title",      fontSize=22, textColor=TEAL,  fontName="Helvetica-Bold",
                        spaceAfter=6, alignment=TA_CENTER)
    s_subtitle   = sty("subtitle",   fontSize=13, textColor=TEAL,  fontName="Helvetica-Bold",
                        spaceAfter=4, alignment=TA_CENTER)
    s_body       = sty("body",       fontSize=10, textColor=BLACK, fontName="Helvetica",
                        spaceAfter=6, leading=16, alignment=TA_JUSTIFY)
    s_section    = sty("section",    fontSize=13, textColor=TEAL,  fontName="Helvetica-Bold",
                        spaceBefore=14, spaceAfter=6)
    s_subsection = sty("subsect",    fontSize=11, textColor=TEAL,  fontName="Helvetica-Bold",
                        spaceBefore=8, spaceAfter=4)
    s_caption    = sty("caption",    fontSize=9,  textColor=GRAY,  fontName="Helvetica-Oblique",
                        spaceAfter=4, alignment=TA_CENTER)
    s_footer     = sty("footer",     fontSize=8,  textColor=GRAY,  fontName="Helvetica",
                        alignment=TA_CENTER)
    s_toc        = sty("toc",        fontSize=10, textColor=BLACK, fontName="Helvetica",
                        spaceAfter=3, leftIndent=10)
    s_toc_title  = sty("toc_title",  fontSize=11, textColor=TEAL,  fontName="Helvetica-Bold",
                        spaceAfter=8)
    s_center     = sty("center",     fontSize=10, textColor=BLACK, fontName="Helvetica",
                        alignment=TA_CENTER)
    s_bold       = sty("bold",       fontSize=10, textColor=BLACK, fontName="Helvetica-Bold",
                        spaceAfter=4)

    def header_bar(text):
        """Barra colorida estilo Interbio como separador de seção."""
        d = Drawing(16*cm, 1.2*cm)
        d.add(Rect(0, 0, 16*cm, 1.2*cm, fillColor=TEAL, strokeColor=None))
        d.add(String(12, 3, text, fontSize=11, fillColor=WHITE, fontName="Helvetica-Bold"))
        return d

    def teal_table(data, col_widths, header_rows=1):
        t = Table(data, colWidths=col_widths)
        style = [
            ("BACKGROUND", (0,0), (-1, header_rows-1), TEAL),
            ("TEXTCOLOR",  (0,0), (-1, header_rows-1), WHITE),
            ("FONTNAME",   (0,0), (-1, header_rows-1), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, header_rows), (-1,-1), [WHITE, LIGHT_GRAY]),
            ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]
        t.setStyle(TableStyle(style))
        return t

    # ── BUFFER ──────────────────────────────────────────────────────────
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm,  bottomMargin=2.5*cm,
        title=f"Inventário GEE {company.name} {inv_year}",
        author="InterMetrics — Interbio & InterGreen")

    story = []
    W = 16 * cm  # largura útil

    # ═══════════════════════════════════════════════════════════════════
    # CAPA
    # ═══════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 1.5*cm))

    # Bloco teal superior
    capa_top = Table([[""], ["INVENTÁRIO DE GASES DE EFEITO ESTUFA"], [f"ANO DE {inv_year}"], [company.name.upper()], [""]],
        colWidths=[W])
    capa_top.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), TEAL),
        ("TEXTCOLOR",  (0,1), (-1,1),  WHITE),
        ("TEXTCOLOR",  (0,2), (-1,2),  colors.HexColor("#a8d8c8")),
        ("TEXTCOLOR",  (0,3), (-1,3),  WHITE),
        ("FONTNAME",   (0,1), (-1,1),  "Helvetica-Bold"),
        ("FONTNAME",   (0,2), (-1,2),  "Helvetica"),
        ("FONTNAME",   (0,3), (-1,3),  "Helvetica-Bold"),
        ("FONTSIZE",   (0,1), (-1,1),  16),
        ("FONTSIZE",   (0,2), (-1,2),  12),
        ("FONTSIZE",   (0,3), (-1,3),  14),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(capa_top)
    story.append(Spacer(1, 0.8*cm))

    # Linha verde
    story.append(HRFlowable(width=W, thickness=4, color=GREEN, spaceAfter=0.5*cm))

    # Identificação capa
    story.append(Paragraph(f"Elaboração: {resp_name}", s_center))
    story.append(Paragraph(f"{resp_job}", s_center))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(f"Interbio Tecnologia Ambiental &amp; InterGreen", sty("capa_empresa",
        fontSize=11, textColor=TEAL, fontName="Helvetica-Bold", alignment=TA_CENTER)))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(f"Gerado em {dt.now().strftime('%d/%m/%Y')}", s_footer))

    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════
    # SUMÁRIO
    # ═══════════════════════════════════════════════════════════════════
    story.append(Paragraph("SUMÁRIO", s_section))
    story.append(HRFlowable(width=W, thickness=1, color=TEAL, spaceAfter=10))
    toc_items = [
        ("1.", "IDENTIFICAÇÃO", "3"),
        ("2.", "INTRODUÇÃO", "3"),
        ("3.", "RESUMO EXECUTIVO", "4"),
        ("4.", "METODOLOGIA", "5"),
        ("5.", "RESULTADOS", "6"),
        ("6.", "RESUMO DOS RESULTADOS", "7"),
        ("7.", "CONCLUSÃO", "8"),
    ]
    for num, title, pg in toc_items:
        row_data = [[Paragraph(f"<b>{num}</b> {title}", s_toc), Paragraph(pg, sty("pg", fontSize=10, alignment=TA_CENTER))]]
        t = Table(row_data, colWidths=[14*cm, 2*cm])
        t.setStyle(TableStyle([
            ("LINEBELOW", (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ═══════════════════════════════════════════════════════════════════
    # 1. IDENTIFICAÇÃO
    # ═══════════════════════════════════════════════════════════════════
    story.append(header_bar("1. IDENTIFICAÇÃO"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("1.1 EMPREENDIMENTO", s_subsection))

    id_data = [
        ["RAZÃO SOCIAL", company.name],
        ["CNPJ", company.cnpj or "—"],
        ["SETOR / RAMO DE ATIVIDADE", company.sector or "—"],
        ["PERÍODO INVENTARIADO", f"1 de janeiro de {inv_year} a 31 de dezembro de {inv_year}"],
    ]
    t = Table(id_data, colWidths=[6*cm, 10*cm])
    t.setStyle(TableStyle([
        ("FONTNAME",   (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("BACKGROUND", (0,0), (0,-1), LIGHT_GRAY),
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("1.2 RESPONSÁVEL TÉCNICO PELA ELABORAÇÃO DO PROJETO", s_subsection))
    resp_data = [
        ["NOME DO RESPONSÁVEL TÉCNICO", resp_name],
        ["EMPRESA RESPONSÁVEL", "Interbio Tecnologia Ambiental"],
        ["PLATAFORMA", "InterMetrics — Plataforma de Métricas & Processos ESG e Carbono"],
    ]
    t2 = Table(resp_data, colWidths=[6*cm, 10*cm])
    t2.setStyle(TableStyle([
        ("FONTNAME",   (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("BACKGROUND", (0,0), (0,-1), LIGHT_GRAY),
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(t2)

    # ═══════════════════════════════════════════════════════════════════
    # 2. INTRODUÇÃO
    # ═══════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 0.5*cm))
    story.append(header_bar("2. INTRODUÇÃO"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "O aumento da concentração de Gases de Efeito Estufa (GEE) na atmosfera tem intensificado o debate "
        "sobre estratégias de mitigação aos efeitos causados pelas mudanças climáticas. O Inventário de Emissões "
        "de Gases de Efeito Estufa é o instrumento gerencial que permite quantificar as emissões de GEE de uma "
        "determinada organização, possibilitando conhecer o perfil das emissões resultantes de suas atividades, "
        "bem como elaborar um plano de ação com estratégias e metas de redução e/ou compensação.", s_body))
    story.append(Paragraph(
        "O Brasil, como membro signatário da Convenção-Quadro das Nações Unidas sobre Mudança do Clima (UNFCCC), "
        "instituiu em 2009 a Política Nacional sobre Mudança do Clima (PNMC), pela Lei nº 12.187, oficializando "
        "o compromisso voluntário de redução de emissões. A realização de inventários de GEE permite às organizações "
        "visualizarem oportunidades no mercado de carbono, atrair novos investimentos e planejar processos com "
        "eficiência econômica, energética e operacional.", s_body))

    # ═══════════════════════════════════════════════════════════════════
    # 3. RESUMO EXECUTIVO
    # ═══════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(header_bar("3. RESUMO EXECUTIVO"))
    story.append(Spacer(1, 0.3*cm))

    escopos_texto = []
    if e1_total > 0:
        escopos_texto.append(f"Escopo 1 (emissões diretas): <b>{e1_total:.2f} tCO₂e</b>")
    if e2_total > 0:
        escopos_texto.append(f"Escopo 2 (energia elétrica adquirida): <b>{e2_total:.2f} tCO₂e</b>")
    if e3_total > 0:
        escopos_texto.append(f"Escopo 3 (cadeia de valor): <b>{e3_total:.2f} tCO₂e</b>")

    story.append(Paragraph(
        f"O presente Inventário avaliou as emissões de GEE das atividades da <b>{company.name}</b> "
        f"para o ano de <b>{inv_year}</b>, com base na metodologia do GHG Protocol. "
        f"As emissões totalizaram <b>{grand_total:.2f} tCO₂e/ano</b>, sendo: "
        + "; ".join(escopos_texto) + ".", s_body))

    # Tabela resumo executivo
    exec_rows = [["ESCOPO", "FONTE DE EMISSÃO", "EMISSÕES (tCO₂e)"]]
    if e1_stationary > 0:
        exec_rows.append(["Escopo 1", "Combustão estacionária", f"{e1_stationary:.2f}"])
    if e1_mobile > 0:
        exec_rows.append(["Escopo 1", "Combustão móvel", f"{e1_mobile:.2f}"])
    if e1_fugitive > 0:
        exec_rows.append(["Escopo 1", "Emissões fugitivas", f"{e1_fugitive:.2f}"])
    if e1_process > 0:
        exec_rows.append(["Escopo 1", "Processo industrial", f"{e1_process:.2f}"])
    if e1_other > 0:
        exec_rows.append(["Escopo 1", "Outras emissões diretas", f"{e1_other:.2f}"])
    if e2_total > 0:
        exec_rows.append(["Escopo 2", "Aquisição de energia elétrica", f"{e2_total:.2f}"])
    if e3_total > 0:
        exec_rows.append(["Escopo 3", "Cadeia de valor", f"{e3_total:.2f}"])
    exec_rows.append(["", "TOTAL", f"{grand_total:.2f}"])

    t_exec = teal_table(exec_rows, [4*cm, 8*cm, 4*cm])
    t_exec.setStyle(TableStyle([
        *t_exec._tblStyle._cmds,
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#e8f4f0")),
    ]))
    story.append(t_exec)
    story.append(Paragraph("Tabela 1: Resumo dos escopos e emissões inventariadas.", s_caption))

    # Gráfico de barras simples com reportlab
    story.append(Spacer(1, 0.4*cm))
    chart_data = []
    chart_labels = []
    if e1_total > 0: chart_data.append(round(e1_total, 2)); chart_labels.append("Escopo 1")
    if e2_total > 0: chart_data.append(round(e2_total, 2)); chart_labels.append("Escopo 2")
    if e3_total > 0: chart_data.append(round(e3_total, 2)); chart_labels.append("Escopo 3")

    if chart_data:
        from reportlab.graphics.shapes import Drawing, String, Rect
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        drawing = Drawing(W, 180)
        bc = VerticalBarChart()
        bc.x = 60; bc.y = 30; bc.height = 130; bc.width = W - 90
        bc.data = [chart_data]
        bc.bars[0].fillColor = TEAL
        bc.valueAxis.valueMin = 0
        bc.valueAxis.valueMax = max(chart_data) * 1.2
        bc.valueAxis.valueStep = max(chart_data) / 5
        bc.valueAxis.labels.fontSize = 8
        bc.categoryAxis.categoryNames = chart_labels
        bc.categoryAxis.labels.fontSize = 9
        bc.categoryAxis.labels.fontName = "Helvetica"
        drawing.add(bc)
        story.append(drawing)
        story.append(Paragraph("Gráfico 1: Total de emissões, em tCO₂e por Escopo.", s_caption))

    # ═══════════════════════════════════════════════════════════════════
    # 4. METODOLOGIA
    # ═══════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(header_bar("4. METODOLOGIA"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "O <b>GHG Protocol</b> (Protocolo de Gases de Efeito Estufa) é a metodologia utilizada, "
        "sendo a ferramenta mais utilizada mundialmente por empresas e governos para entender, quantificar "
        "e gerenciar emissões de GEE. Desenvolvida pelo World Resources Institute (WRI) em associação com "
        "o World Business Council for Sustainable Development (WBCSD), é compatível com as normas ISO e "
        "com as metodologias do IPCC.", s_body))
    story.append(Paragraph(
        "Os fatores de emissão utilizados neste inventário são provenientes das seguintes fontes oficiais: "
        "<b>MCTI 2024</b> (Ministério da Ciência, Tecnologia e Inovações), <b>SEEG v12</b> (Sistema de "
        "Estimativas de Emissões e Remoções de Gases de Efeito Estufa) e <b>IPCC AR6</b> (Sexto Relatório "
        "de Avaliação do Painel Intergovernamental sobre Mudanças Climáticas). Para emissões fugitivas, "
        "os Potenciais de Aquecimento Global (GWP) seguem o IPCC AR5 (100 anos).", s_body))

    story.append(Paragraph("4.1 PRINCÍPIOS DO INVENTÁRIO", s_subsection))
    princ_data = [
        ["PRINCÍPIO", "DESCRIÇÃO"],
        ["Relevância", "O inventário reflete com exatidão as emissões da empresa e serve às necessidades de decisão."],
        ["Integralidade", "Todas as fontes dentro dos limites do inventário são contabilizadas."],
        ["Consistência", "Metodologia e limites são consistentes para todos os anos e fontes."],
        ["Transparência", "Premissas e metodologias são claramente explicadas."],
        ["Exatidão", "Estimativas são precisas, sem desvios sistemáticos acima ou abaixo do real."],
    ]
    t_princ = teal_table(princ_data, [4*cm, 12*cm])
    story.append(t_princ)

    story.append(Paragraph("4.2 GASES CONSIDERADOS", s_subsection))
    # GWPs usados
    gases_data = [
        ["FÓRMULA", "NOME COMUM", "GWP (AR5/SAR)"],
        ["CO₂", "Dióxido de carbono", "1"],
        ["CH₄", "Metano", "28"],
        ["N₂O", "Óxido nitroso", "265"],
        ["R-410A", "HFC (mistura)", "2.088"],
        ["R-407C", "HFC (mistura)", "1.774"],
        ["SF₆", "Hexafluoreto de enxofre", "23.500"],
    ]
    t_gases = teal_table(gases_data, [3*cm, 8*cm, 5*cm])
    story.append(t_gases)
    story.append(Paragraph("Tabela 2: Potencial de Aquecimento Global (GWP) dos principais gases inventariados. Fonte: MCTI 2024 / IPCC AR5.", s_caption))

    # ═══════════════════════════════════════════════════════════════════
    # 5. RESULTADOS
    # ═══════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(header_bar("5. RESULTADOS"))
    story.append(Spacer(1, 0.3*cm))

    # Resultados por escopo
    scopes_info = [(1,"ESCOPO 1 — EMISSÕES DIRETAS"), (2,"ESCOPO 2 — ENERGIA ELÉTRICA"), (3,"ESCOPO 3 — CADEIA DE VALOR")]
    for scope_num, scope_label in scopes_info:
        scope_emissions = [e for e in emissions if e.scope == scope_num]
        if not scope_emissions:
            continue
        story.append(Paragraph(scope_label, s_subsection))
        scope_total = sum(e.total_co2e for e in scope_emissions)

        # Categorias
        cats = {}
        for e in scope_emissions:
            cats.setdefault(e.category, []).append(e)
        for cat, cat_emis in cats.items():
            cat_total = sum(e.total_co2e for e in cat_emis)
            story.append(Paragraph(f"<b>{cat}</b> — {cat_total:.4f} tCO₂e", s_bold))
            detail_data = [["FONTE", "COMBUSTÍVEL/GÁS", "QUANTIDADE", "UNIDADE", "FE", "tCO₂e"]]
            for e in cat_emis[:20]:
                detail_data.append([
                    e.source_name[:30],
                    (e.fuel_type or "—")[:20],
                    f"{e.quantity:.2f}",
                    e.unit or "—",
                    f"{e.emission_factor:.4f}",
                    f"{e.total_co2e:.4f}"
                ])
            t_detail = teal_table(detail_data, [4.5*cm, 3.5*cm, 2*cm, 1.5*cm, 2*cm, 2.5*cm])
            story.append(t_detail)
            story.append(Spacer(1, 0.2*cm))

        story.append(Paragraph(f"<b>Total {scope_label.split('—')[0].strip()}:</b> {scope_total:.2f} tCO₂e", s_bold))
        story.append(HRFlowable(width=W, thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))

    # ═══════════════════════════════════════════════════════════════════
    # 6. RESUMO DOS RESULTADOS
    # ═══════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(header_bar("6. RESUMO DOS RESULTADOS"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"As emissões totais das atividades da <b>{company.name}</b> no ano de <b>{inv_year}</b> "
        f"totalizaram <b>{grand_total:.2f} tCO₂e</b>, conforme apresentado na Tabela 4 e no Gráfico 2.", s_body))

    # Tabela desagregada completa
    full_rows = [["ESCOPO", "FONTE DE EMISSÃO", "EMISSÕES (tCO₂e)", "CONTRIBUIÇÃO (%)"]]
    all_cats = {}
    for e in emissions:
        key = (e.scope, e.category)
        all_cats[key] = all_cats.get(key, 0) + e.total_co2e
    for (sc, cat), total in sorted(all_cats.items()):
        pct = (total / grand_total * 100) if grand_total > 0 else 0
        full_rows.append([f"Escopo {sc}", cat or "—", f"{total:.2f}", f"{pct:.1f}%"])
    full_rows.append(["", "TOTAL", f"{grand_total:.2f}", "100%"])

    t_full = teal_table(full_rows, [3*cm, 7*cm, 3.5*cm, 2.5*cm])
    t_full.setStyle(TableStyle([
        *t_full._tblStyle._cmds,
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#e8f4f0")),
    ]))
    story.append(t_full)
    story.append(Paragraph("Tabela 4: Emissões de GEE desagregadas por Escopo, em tCO₂e.", s_caption))

    # ═══════════════════════════════════════════════════════════════════
    # 7. CONCLUSÃO
    # ═══════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(header_bar("7. CONCLUSÃO"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"O Inventário de emissões de GEE da <b>{company.name}</b> demonstrou uma emissão total de "
        f"<b>{grand_total:.2f} tCO₂e</b> no ano de <b>{inv_year}</b>. "
        "De acordo com o Art. 30 da Lei nº 15.042, de 11 de dezembro de 2024, o enquadramento nas "
        "obrigações do Sistema Brasileiro de Comércio de Emissões de Gases de Efeito Estufa (SBCE) "
        "se aplica apenas a operadores que emitam acima de 10.000 tCO₂e por ano.", s_body))

    if grand_total < 10000:
        story.append(Paragraph(
            f"Com base nos dados apurados ({grand_total:.2f} tCO₂e) e conforme os critérios legais "
            "estabelecidos, a empresa não se enquadra nos limites de obrigatoriedade definidos pelo SBCE, "
            "estando dispensada das exigências regulatórias previstas no referido artigo.", s_body))
    else:
        story.append(Paragraph(
            f"Com base nos dados apurados ({grand_total:.2f} tCO₂e), a empresa <b>se enquadra</b> nos "
            "limites de obrigatoriedade definidos pelo SBCE, devendo cumprir as exigências regulatórias "
            "previstas, incluindo envio de plano de monitoramento e relatórios de emissões.", s_body))

    story.append(Paragraph(
        "O inventário de GEE é a primeira etapa do diagnóstico e deve ser continuamente aprimorado. "
        "Recomenda-se a elaboração de metas de curto, médio e longo prazo para redução e/ou compensação "
        "das emissões inventariadas, além da expansão do inventário para o Escopo 3 nos próximos ciclos.", s_body))

    # Rodapé final
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width=W, thickness=1, color=TEAL, spaceAfter=8))
    story.append(Paragraph(
        f"Documento gerado pela plataforma <b>InterMetrics</b> — Interbio Tecnologia Ambiental &amp; InterGreen<br/>"
        f"Data de geração: {dt.now().strftime('%d/%m/%Y às %H:%M')} | Ano-base: {inv_year}",
        s_footer))

    # ── BUILD ────────────────────────────────────────────────────────
    doc.build(story)
    buf.seek(0)

    filename = f"Inventario_GEE_{company.name.replace(' ','_')}_{inv_year}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")
