import csv
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import notificaciones
from utils.statute_parser import process_statute_pdf

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INSTANCE_DIR = BASE_DIR / "instance"
PAGE_IMAGES_DIR = BASE_DIR / "static" / "generated" / "pages"
STATUTE_PDF = DATA_DIR / "estatuto.pdf"
STATUTE_STRUCTURE = DATA_DIR / "estatuto_structure.json"
SQLITE_PATH = Path(os.getenv("SQLITE_PATH") or INSTANCE_DIR / "app.db")  # configurable para pruebas

for directory in (DATA_DIR, INSTANCE_DIR, PAGE_IMAGES_DIR):
    directory.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
DB_ENGINE = (os.getenv("DB_ENGINE") or "sqlite").strip().lower()
USE_POSTGRES = DB_ENGINE in {"postgres", "postgresql"}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _env_secret(name: str) -> str:
    """Lee un secreto quitando comillas envolventes (docker run --env-file las deja literales)."""
    value = (os.getenv(name) or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


# En producción (contenedores Docker) la configuración insegura impide arrancar.
IS_PRODUCTION = (os.getenv("APP_ENV") or "development").strip().lower() == "production"
_PLACEHOLDER_SECRETS = {
    "", "x", "coopetrol-local-dev-key", "cambie-esta-clave", "cambie-esta-clave-para-produccion",
}


def _load_secret_key() -> str:
    key = _env_secret("SECRET_KEY")
    if key in _PLACEHOLDER_SECRETS or len(key) < 32:
        if IS_PRODUCTION:
            raise RuntimeError(
                "SECRET_KEY no está definida o es insegura (mínimo 32 caracteres aleatorios)."
            )
        # Desarrollo local: clave aleatoria por ejecución, nunca una clave fija conocida.
        print("Advertencia: SECRET_KEY insegura; se usa una clave temporal (sesiones se pierden al reiniciar).")
        return secrets.token_hex(32)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    APP_NAME=os.getenv("APP_NAME") or "Propuestas de ajuste al Estatuto",
    # La contraseña en texto plano solo se admite en desarrollo local.
    ADMIN_PASSWORD="" if IS_PRODUCTION else _env_secret("ADMIN_PASSWORD"),
    ADMIN_PASSWORD_HASH=_env_secret("ADMIN_PASSWORD_HASH"),
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
    # Ruta base de publicación (p. ej. /estatutos detrás del proxy). También fija
    # la ruta de la cookie de sesión para no mezclarla con otras aplicaciones.
    APPLICATION_ROOT=os.getenv("APPLICATION_ROOT") or "/",
    SESSION_COOKIE_NAME=os.getenv("SESSION_COOKIE_NAME") or "session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", IS_PRODUCTION),
)

if IS_PRODUCTION and not app.config["ADMIN_PASSWORD_HASH"]:
    raise RuntimeError("ADMIN_PASSWORD_HASH no está definida: el panel administrativo quedaría sin protección.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
audit_log = logging.getLogger("estatutos.auditoria")

# Versión desplegada: permite saber qué está corriendo y a cuál volver si algo falla.
try:
    APP_VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip() or "desconocida"
except OSError:
    APP_VERSION = "desconocida"
audit_log.info("Iniciando aplicación versión=%s", APP_VERSION)

# Detrás de un proxy inverso (nginx/IIS) se respetan X-Forwarded-For/Proto/Host/Port.
if _env_flag("BEHIND_PROXY", False):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


app.permanent_session_lifetime = timedelta(minutes=30)
DEFAULT_SETTINGS = {
    "admission_url": "https://www.coopetrol.coop/admision-virtual/",
    "privacy_url": "https://www.coopetrol.coop/wp-content/uploads/2024/09/Manual_de_Politicas_y_Procedimientos_para_el_Tratamiento_de_Datos_Personales_WEB.pdf",
    "statute_title": "Estatuto Coopetrol",
}


# -----------------------------------------------------------------------------
# Database layer: SQLite by default, PostgreSQL only if DATABASE_URL is defined.
# -----------------------------------------------------------------------------
def _connect_database():
    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                'DATABASE_URL apunta a PostgreSQL, pero falta psycopg. Instale: pip install "psycopg[binary]"'
            ) from exc
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect_database()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _sql(sql: str) -> str:
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


def fetchone(sql: str, params=()):
    cur = get_db().cursor()
    cur.execute(_sql(sql), params)
    return cur.fetchone()


def fetchall(sql: str, params=()):
    cur = get_db().cursor()
    cur.execute(_sql(sql), params)
    return cur.fetchall()


def execute(sql: str, params=(), commit=True):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(_sql(sql), params)
    if commit:
        conn.commit()
    return cur


def _direct_execute(conn, sql: str, params=()):
    cur = conn.cursor()
    cur.execute(sql.replace("?", "%s") if USE_POSTGRES else sql, params)
    return cur


def init_db():
    conn = _connect_database()
    if USE_POSTGRES:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS associates (
                cedula TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id BIGSERIAL PRIMARY KEY,
                cedula TEXT NOT NULL,
                nombre TEXT NOT NULL,
                capitulo TEXT,
                articulo TEXT,
                paragrafo TEXT,
                numeral TEXT,
                literal TEXT,
                pagina INTEGER,
                referencia_texto TEXT,
                tipo_cambio TEXT NOT NULL,
                propuesta TEXT NOT NULL,
                consentimiento_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
        ]
    else:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS associates (
                cedula TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cedula TEXT NOT NULL,
                nombre TEXT NOT NULL,
                capitulo TEXT,
                articulo TEXT,
                paragrafo TEXT,
                numeral TEXT,
                literal TEXT,
                pagina INTEGER,
                referencia_texto TEXT,
                tipo_cambio TEXT NOT NULL,
                propuesta TEXT NOT NULL,
                consentimiento_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
        ]

    try:
        for statement in statements:
            _direct_execute(conn, statement)
        for key, value in DEFAULT_SETTINGS.items():
            _direct_execute(
                conn,
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


init_db()


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------
def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(24)
        session["_csrf_token"] = token
    return token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        expected = session.get("_csrf_token")
        received = request.form.get("_csrf_token")
        if not expected or not received or not secrets.compare_digest(expected, received):
            return "Solicitud inválida: token de seguridad no válido.", 400


def csp_nonce():
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


# HSTS aplica a todo el host (todos los puertos): solo se activa si HSTS_MAX_AGE > 0
# y ningún otro servicio de sistemas.coopetrol.coop se publica por http://.
HSTS_MAX_AGE = int(os.getenv("HSTS_MAX_AGE") or 0)


@app.after_request
def security_headers(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{csp_nonce()}'; "
        "style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'",
    )
    if HSTS_MAX_AGE > 0:
        headers.setdefault("Strict-Transport-Security", f"max-age={HSTS_MAX_AGE}")
    # Las páginas y descargas pueden contener datos personales: no se guardan en caché.
    # Los archivos estáticos (CSS, JS, imágenes) conservan su caché normal.
    if request.endpoint != "static":
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        headers["Pragma"] = "no-cache"
    return response


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "csp_nonce": csp_nonce,
        "settings": get_settings(),
        "app_name": app.config["APP_NAME"],
        "app_version": APP_VERSION,
    }


def normalize_cedula(value) -> str:
    return re.sub(r"\D", "", "" if value is None else str(value))


def normalize_fecha_expedicion(value) -> str:
    """Devuelve la fecha en YYYY-MM-DD o cadena vacía si no es válida."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return ""

    # Excel/CSV puede traer hora junto a la fecha.
    if " " in text and re.match(r"^\d{4}-\d{2}-\d{2} ", text):
        text = text.split(" ", 1)[0]

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def get_settings():
    rows = fetchall("SELECT key, value FROM settings")
    values = {row["key"]: row["value"] for row in rows}
    for key, value in DEFAULT_SETTINGS.items():
        values.setdefault(key, value)
    return values


def save_setting(key: str, value: str):
    execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def load_structure():
    if not STATUTE_STRUCTURE.exists():
        return {"pages": [], "items": [], "article_count": 0}
    try:
        return json.loads(STATUTE_STRUCTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"pages": [], "items": [], "article_count": 0}


def ensure_statute_processed():
    if not STATUTE_PDF.exists():
        return
    if STATUTE_STRUCTURE.exists() and any(PAGE_IMAGES_DIR.glob("page_*.png")):
        return
    process_statute_pdf(str(STATUTE_PDF), str(STATUTE_STRUCTURE), str(PAGE_IMAGES_DIR))


def admin_guard():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    return None


def member_guard():
    if not session.get("member_cedula"):
        flash("Primero valide su condición de asociado.", "warning")
        return redirect(url_for("index"))
    return None


def _admin_password_matches(password: str) -> bool:
    if not password:
        return False
    configured_hash = app.config["ADMIN_PASSWORD_HASH"]
    if configured_hash:
        try:
            return check_password_hash(configured_hash, password)
        except ValueError:
            audit_log.error("ADMIN_PASSWORD_HASH tiene un formato inválido.")
            return False
    plain = app.config["ADMIN_PASSWORD"]
    # Sin contraseña configurada el acceso queda cerrado (antes "" == "" permitía entrar).
    if not plain:
        return False
    return secrets.compare_digest(password.encode("utf-8"), plain.encode("utf-8"))


class AttemptLimiter:
    """Límite de intentos fallidos en memoria por clave (IP, cédula...)."""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._attempts = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, bucket, now):
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._attempts.get(key)
            if not bucket:
                return False
            self._prune(bucket, now)
            return len(bucket) >= self.max_attempts

    def register_failure(self, key: str):
        now = time.monotonic()
        with self._lock:
            bucket = self._attempts[key]
            self._prune(bucket, now)
            bucket.append(now)
            if len(self._attempts) > 50000:  # evita crecimiento ilimitado de memoria
                self._attempts.clear()

    def reset(self, key: str):
        with self._lock:
            self._attempts.pop(key, None)


admin_login_limiter = AttemptLimiter(max_attempts=5, window_seconds=15 * 60)
member_ip_limiter = AttemptLimiter(max_attempts=10, window_seconds=15 * 60)
member_cedula_limiter = AttemptLimiter(max_attempts=5, window_seconds=15 * 60)


def client_ip() -> str:
    return request.remote_addr or "desconocida"


def safe_external_url(value: str) -> bool:
    """Solo enlaces https:// (o http://) absolutos; evita javascript:, data:, etc."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc) and len(value) <= 500


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


def excel_safe(value):
    """Neutraliza inyección de fórmulas (CSV/Excel injection) en textos exportados."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _associate_header_indexes(header):
    normalized = [str(v).strip().lower() if v is not None else "" for v in header]
    aliases = {
        "cedula": {"cedula", "cédula", "documento", "identificacion", "identificación", "numero_documento"},
        "nombre": {"nombre", "nombres", "nombre_completo", "nombres_apellidos", "nombres y apellidos"},
        "fecha_expedicion": {"fecha_expedicion", "fecha expedicion", "fecha de expedicion", "fecha_expedición", "fecha de expedición"},
        # Opcional: si no viene, se conserva el correo ya cargado.
        "correo": {"correo", "correo_electronico", "correo electronico", "correo electrónico", "correo_electrónico", "email", "e-mail", "mail"},
    }
    result = {}
    for field, choices in aliases.items():
        for i, value in enumerate(normalized):
            if value in choices:
                result[field] = i
                break
    missing = [field for field in ("cedula", "nombre", "fecha_expedicion") if field not in result]
    if missing:
        raise ValueError(
            "La base debe contener las columnas cedula, nombre y fecha_expedicion. "
            f"Faltan: {', '.join(missing)}."
        )
    return result


def _read_csv_associates(file_storage):
    raw = file_storage.read()
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;	|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        return []
    idx = _associate_header_indexes(header)
    rows = []
    for row in reader:
        def value(field):
            pos = idx.get(field)
            return row[pos] if pos is not None and pos < len(row) else None
        rows.append({
            "cedula": value("cedula"),
            "nombre": value("nombre"),
            "fecha_expedicion": value("fecha_expedicion"),
            "correo": value("correo"),
        })
    return rows


def _read_excel_associates(file_storage):
    wb = load_workbook(file_storage, read_only=True, data_only=True)
    ws = wb.active
    iterator = ws.iter_rows(values_only=True)
    try:
        header = next(iterator)
    except StopIteration:
        return []
    idx = _associate_header_indexes(header)
    rows = []
    for row in iterator:
        def value(field):
            pos = idx.get(field)
            return row[pos] if pos is not None and pos < len(row) else None
        rows.append({
            "cedula": value("cedula"),
            "nombre": value("nombre"),
            "fecha_expedicion": value("fecha_expedicion"),
            "correo": value("correo"),
        })
    return rows



# Campos agregados al formulario de propuestas (nombres fijos, no provienen del usuario).
PROPOSAL_EXTRA_COLUMNS = ("justificacion", "modalidad_propuesta", "grupo_propuesta")


def ensure_feature_schema():
    """Extiende la base existente sin borrar información."""
    conn = _connect_database()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("ALTER TABLE associates ADD COLUMN IF NOT EXISTS nombre TEXT")
            cur.execute("ALTER TABLE associates ADD COLUMN IF NOT EXISTS fecha_expedicion DATE")
            cur.execute("ALTER TABLE associates ADD COLUMN IF NOT EXISTS correo TEXT")
            for column in PROPOSAL_EXTRA_COLUMNS:
                cur.execute(f"ALTER TABLE proposals ADD COLUMN IF NOT EXISTS {column} TEXT")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id BIGSERIAL PRIMARY KEY,
                    cedula TEXT NOT NULL,
                    destino TEXT,
                    proposal_ids TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    intentos INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS statute_restrictions (
                    id BIGSERIAL PRIMARY KEY,
                    capitulo TEXT,
                    articulo TEXT,
                    paragrafo TEXT,
                    numeral TEXT,
                    literal TEXT,
                    mensaje TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        else:
            existing = {row[1] for row in cur.execute("PRAGMA table_info(associates)").fetchall()}
            if "nombre" not in existing:
                cur.execute("ALTER TABLE associates ADD COLUMN nombre TEXT")
            if "fecha_expedicion" not in existing:
                cur.execute("ALTER TABLE associates ADD COLUMN fecha_expedicion TEXT")
            if "correo" not in existing:
                cur.execute("ALTER TABLE associates ADD COLUMN correo TEXT")
            proposal_columns = {row[1] for row in cur.execute("PRAGMA table_info(proposals)").fetchall()}
            for column in PROPOSAL_EXTRA_COLUMNS:
                if column not in proposal_columns:
                    cur.execute(f"ALTER TABLE proposals ADD COLUMN {column} TEXT")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cedula TEXT NOT NULL,
                    destino TEXT,
                    proposal_ids TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    intentos INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS statute_restrictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capitulo TEXT,
                    articulo TEXT,
                    paragrafo TEXT,
                    numeral TEXT,
                    literal TEXT,
                    mensaje TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )

        # Regla inicial solicitada: se crea solo la primera vez.
        # Después de eso el administrador tiene control total: si la desactiva,
        # no se vuelve a crear al reiniciar la aplicación.
        cur.execute("SELECT COUNT(*) AS total FROM statute_restrictions")
        count_row = cur.fetchone()
        total_rules = int(count_row["total"] if hasattr(count_row, "keys") else count_row[0])
        if total_rules == 0:
            cur.execute(
                _sql(
                    "INSERT INTO statute_restrictions "
                    "(capitulo, articulo, paragrafo, numeral, literal, mensaje, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1)"
                ),
                (None, "1", None, None, None, "Este texto no está habilitado para cambios."),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_statute_restrictions():
    rows = fetchall(
        """
        SELECT id, capitulo, articulo, paragrafo, numeral, literal, mensaje
        FROM statute_restrictions
        WHERE active = 1
        ORDER BY id
        """
    )
    return [dict(row) for row in rows]


def _ref_value(value):
    return str(value or "").strip().casefold()


def find_blocking_restriction(capitulo="", articulo="", paragrafo="", numeral="", literal=""):
    reference = {
        "capitulo": _ref_value(capitulo),
        "articulo": _ref_value(articulo),
        "paragrafo": _ref_value(paragrafo),
        "numeral": _ref_value(numeral),
        "literal": _ref_value(literal),
    }
    for rule in get_statute_restrictions():
        matches = True
        for field in ("capitulo", "articulo", "paragrafo", "numeral", "literal"):
            required = _ref_value(rule.get(field))
            if required and required != reference[field]:
                matches = False
                break
        if matches:
            return rule
    return None


ensure_feature_schema()

try:
    ensure_statute_processed()
except Exception as exc:
    # The app must still start; administration can retry processing later.
    print(f"Advertencia al procesar el Estatuto: {exc}")


# -----------------------------------------------------------------------------
# Public / associate experience
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    if session.get("member_cedula"):
        return redirect(url_for("proposal"))
    return render_template("index.html", statute_available=STATUTE_PDF.exists())


MEMBER_DOCUMENT_RE = re.compile(r"[0-9]{1,10}")


@app.route("/validar", methods=["POST"])
def validate_member():
    cedula_raw = (request.form.get("cedula") or "").strip()
    cedula = normalize_cedula(cedula_raw)
    fecha_expedicion = normalize_fecha_expedicion(request.form.get("fecha_expedicion"))
    consent = request.form.get("consent") == "on"

    if not cedula_raw:
        flash("Ingrese su número de documento.", "error")
        return redirect(url_for("index"))
    # Misma regla que el formulario: solo dígitos, máximo 10.
    if not MEMBER_DOCUMENT_RE.fullmatch(cedula_raw):
        flash("El número de documento solo admite números, máximo 10 dígitos.", "error")
        return redirect(url_for("index"))
    if not fecha_expedicion:
        flash("Ingrese una fecha de expedición válida.", "error")
        return redirect(url_for("index"))
    if not consent:
        flash("Debe autorizar el tratamiento de datos personales para continuar.", "error")
        return redirect(url_for("index"))

    ip = client_ip()
    if member_ip_limiter.is_blocked(ip) or member_cedula_limiter.is_blocked(cedula):
        audit_log.warning("Validación bloqueada por exceso de intentos ip=%s", ip)
        flash("Demasiados intentos de validación. Espere 15 minutos e intente nuevamente.", "error")
        return redirect(url_for("index"))

    associate = fetchone(
        "SELECT cedula, nombre, fecha_expedicion FROM associates WHERE cedula = ?",
        (cedula,),
    )
    stored_date = normalize_fecha_expedicion(associate["fecha_expedicion"]) if associate else ""
    nombre = ((associate["nombre"] if associate else "") or "").strip()

    # Respuesta única para cédula inexistente, fecha errada o registro incompleto:
    # así no se puede averiguar quién es asociado de Coopetrol (Ley 1581 de 2012).
    if not associate or not stored_date or stored_date != fecha_expedicion or not nombre:
        member_ip_limiter.register_failure(ip)
        member_cedula_limiter.register_failure(cedula)
        if associate and stored_date == fecha_expedicion and not nombre:
            audit_log.warning("Asociado sin nombre en la base (validación rechazada).")
        return render_template("not_member.html")

    member_ip_limiter.reset(ip)
    member_cedula_limiter.reset(cedula)
    # Sesión nueva al autenticarse (evita fijación de sesión).
    session.clear()
    session["member_cedula"] = cedula
    session["member_name"] = nombre
    session["member_fecha_expedicion"] = fecha_expedicion
    session["member_consent_at"] = datetime.now().isoformat(timespec="seconds")
    session.permanent = True
    return redirect(url_for("proposal"))


TIPOS_CAMBIO = ("Modificación", "Adición", "Eliminación")
MODALIDADES_PROPUESTA = ("Propuesta individual", "Propuesta de grupo")
GRUPOS_PROPUESTA = ("Consejo de Administración", "Agencia", "Administración")
MAX_PROPUESTA = 10000


@app.route("/propuesta", methods=["GET", "POST"])
def proposal():
    guard = member_guard()
    if guard:
        return guard

    cedula = session["member_cedula"]
    associate = fetchone("SELECT cedula, nombre FROM associates WHERE cedula = ?", (cedula,))
    if not associate:
        session.pop("member_cedula", None)
        session.pop("member_name", None)
        flash("Su registro ya no se encuentra en la base vigente. Valide nuevamente.", "warning")
        return redirect(url_for("index"))

    nombre = (associate["nombre"] or "").strip()
    count_row = fetchone("SELECT COUNT(*) AS total FROM proposals WHERE cedula = ?", (cedula,))
    previous_count = int(count_row["total"])
    structure = load_structure()
    restrictions = get_statute_restrictions()
    form = {}

    if request.method == "POST":
        form = request.form.to_dict()
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        tipo_cambio = (request.form.get("tipo_cambio") or "").strip()
        propuesta_texto = (request.form.get("propuesta") or "").strip()
        capitulo = (request.form.get("capitulo") or "").strip()
        articulo = (request.form.get("articulo") or "").strip()
        paragrafo = (request.form.get("paragrafo") or "").strip()
        numeral = (request.form.get("numeral") or "").strip()
        literal = (request.form.get("literal") or "").strip()
        pagina_raw = (request.form.get("pagina") or "").strip()
        referencia_texto = (request.form.get("referencia_texto") or "").strip()
        justificacion = (request.form.get("justificacion") or "").strip()
        modalidad_propuesta = (request.form.get("modalidad_propuesta") or "").strip()
        grupo_propuesta = (request.form.get("grupo_propuesta") or "").strip()

        errors = []
        if not any([capitulo, articulo, paragrafo, numeral, literal]):
            errors.append("Seleccione en el Estatuto el apartado que desea ajustar.")
        else:
            blocked = find_blocking_restriction(capitulo, articulo, paragrafo, numeral, literal)
            if blocked:
                errors.append(blocked.get("mensaje") or "Este texto no está habilitado para cambios.")
        if tipo_cambio not in TIPOS_CAMBIO:
            errors.append("Seleccione el tipo de solicitud.")
        if len(propuesta_texto) < 10:
            errors.append("Describa el ajuste propuesto con al menos 10 caracteres.")
        if not justificacion:
            errors.append("La justificación de la propuesta es obligatoria.")
        if modalidad_propuesta not in MODALIDADES_PROPUESTA:
            errors.append("Seleccione una modalidad de propuesta válida.")
        elif modalidad_propuesta == "Propuesta de grupo":
            if grupo_propuesta not in GRUPOS_PROPUESTA:
                errors.append("Seleccione el grupo que presenta la propuesta.")
        else:
            grupo_propuesta = None
        if len(propuesta_texto) > MAX_PROPUESTA or len(justificacion) > MAX_PROPUESTA:
            errors.append(f"La propuesta y la justificación admiten máximo {MAX_PROPUESTA} caracteres cada una.")
        if any(len(v) > 60 for v in (capitulo, articulo, paragrafo, numeral, literal)) \
                or len(referencia_texto) > 4000 or len(pagina_raw) > 5:
            errors.append("La referencia seleccionada no es válida. Selecciónela nuevamente en el Estatuto.")

        if errors:
            if is_ajax:
                return jsonify({"ok": False, "errors": errors}), 422
            for message in errors:
                flash(message, "error")
        else:
            created_at = datetime.now().isoformat(timespec="seconds")
            consentimiento_at = session.get("member_consent_at") or created_at
            pagina = int(pagina_raw) if pagina_raw.isdigit() else None

            if USE_POSTGRES:
                cur = execute(
                    '\n                    INSERT INTO proposals (cedula, nombre, capitulo, articulo, paragrafo, numeral, literal, pagina, referencia_texto, tipo_cambio, propuesta, consentimiento_at, created_at, justificacion, modalidad_propuesta, grupo_propuesta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                    RETURNING id\n                    ',
                    (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta_texto,
                        consentimiento_at, created_at,
                        justificacion,
                        modalidad_propuesta,
                        grupo_propuesta),
                    commit=False,
                )
                proposal_id = cur.fetchone()["id"]
                get_db().commit()
            else:
                cur = execute(
                    '\n                    INSERT INTO proposals (cedula, nombre, capitulo, articulo, paragrafo, numeral, literal, pagina, referencia_texto, tipo_cambio, propuesta, consentimiento_at, created_at, justificacion, modalidad_propuesta, grupo_propuesta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                    ',
                    (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta_texto,
                        consentimiento_at, created_at,
                        justificacion,
                        modalidad_propuesta,
                        grupo_propuesta),
                )
                proposal_id = cur.lastrowid

            # Registros de esta sesión: se confirman en un solo correo al finalizar.
            session_ids = list(session.get("proposal_ids") or [])
            session_ids.append(int(proposal_id))
            session["proposal_ids"] = session_ids[-100:]

            if is_ajax:
                updated_count = int(
                    fetchone("SELECT COUNT(*) AS total FROM proposals WHERE cedula = ?", (cedula,))["total"]
                )
                return jsonify({
                    "ok": True,
                    "proposal_id": proposal_id,
                    "previous_count": updated_count,
                    "message": "Recomendación registrada correctamente.",
                })

            return redirect(url_for("thanks", proposal_id=proposal_id))

    return render_template(
        "proposal.html",
        cedula=cedula,
        nombre=nombre,
        previous_count=previous_count,
        statute_available=STATUTE_PDF.exists(),
        statute_data=structure,
        statute_restrictions=restrictions,
        form=form,
    )


@app.route("/gracias/<int:proposal_id>")
def thanks(proposal_id):
    guard = member_guard()
    if guard:
        return guard
    # Un asociado solo puede ver la confirmación de sus propias propuestas.
    own = fetchone(
        "SELECT id FROM proposals WHERE id = ? AND cedula = ?",
        (proposal_id, session["member_cedula"]),
    )
    if not own:
        return redirect(url_for("proposal"))
    return render_template("thanks.html", proposal_id=proposal_id)


@app.route("/estatuto.pdf")
def statute_pdf():
    if not STATUTE_PDF.exists():
        return "No hay Estatuto cargado.", 404
    return send_file(STATUTE_PDF, mimetype="application/pdf", conditional=True)


@app.route("/salir", methods=["POST"])
def member_logout():
    session.clear()
    flash("Sesión finalizada.", "success")
    return redirect(url_for("index"))


def _record_notification_result(notification_id):
    """Callback del hilo de envío: guarda el resultado con su propia conexión."""

    def done(estado, intentos, error):
        conn = None
        try:
            conn = _connect_database()
            _direct_execute(
                conn,
                "UPDATE notifications SET estado = ?, intentos = ?, error = ?, sent_at = ? WHERE id = ?",
                (
                    estado, intentos, error or None,
                    datetime.now().isoformat(timespec="seconds") if estado == "enviado" else None,
                    notification_id,
                ),
            )
            conn.commit()
        except Exception:
            audit_log.exception("No fue posible registrar el resultado de la notificación id=%s", notification_id)
        finally:
            if conn is not None:
                conn.close()
        audit_log.info("Notificación id=%s estado=%s intentos=%s", notification_id, estado, intentos)

    return done


def notify_registration_finished(cedula, proposal_ids):
    """Registra y envía (en segundo plano) la confirmación del registro.

    Devuelve el correo enmascarado si el envío quedó en curso, o "" si no se envía.
    Nunca lanza excepción: un fallo de correo no debe impedir finalizar el registro.
    """
    try:
        placeholders = ", ".join("?" for _ in proposal_ids)
        proposals = [
            dict(row) for row in fetchall(
                "SELECT id, capitulo, articulo, paragrafo, numeral, literal, tipo_cambio, created_at "
                f"FROM proposals WHERE cedula = ? AND id IN ({placeholders}) ORDER BY id",
                (cedula, *proposal_ids),
            )
        ]
        if not proposals:
            return ""

        associate = fetchone("SELECT nombre, correo FROM associates WHERE cedula = ?", (cedula,))
        nombre = ((associate["nombre"] if associate else "") or session.get("member_name") or "").strip()
        destino = notificaciones.normalizar_correo(associate["correo"] if associate else "")
        ids_text = ",".join(str(p["id"]) for p in proposals)
        now = datetime.now().isoformat(timespec="seconds")

        # Doble clic o reenvío del formulario: la cookie de sesión aún trae los mismos IDs.
        if fetchone(
            "SELECT id FROM notifications WHERE cedula = ? AND proposal_ids = ?",
            (cedula, ids_text),
        ):
            return ""

        if not destino:
            estado = "sin_destino"
        elif not notificaciones.correo_configurado():
            estado = "deshabilitado"
        else:
            estado = "pendiente"

        insert_sql = (
            "INSERT INTO notifications (cedula, destino, proposal_ids, estado, intentos, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)"
        )
        params = (cedula, destino or None, ids_text, estado, now)
        if USE_POSTGRES:
            cur = execute(insert_sql + " RETURNING id", params, commit=False)
            notification_id = cur.fetchone()["id"]
            get_db().commit()
        else:
            notification_id = execute(insert_sql, params).lastrowid

        audit_log.info(
            "Finalización de registro notificacion=%s estado=%s propuestas=%s destino=%s",
            notification_id, estado, ids_text, notificaciones.enmascarar_correo(destino),
        )
        if estado != "pendiente":
            return ""

        message = notificaciones.construir_mensaje(destino, nombre, proposals, app.config["APP_NAME"])
        notificaciones.enviar_en_segundo_plano(message, _record_notification_result(notification_id))
        return notificaciones.enmascarar_correo(destino)
    except Exception:
        audit_log.exception("Error preparando la notificación de finalización de registro")
        return ""


@app.route("/finalizar", methods=["POST"])
def member_finish():
    """Botón "Finalizar registro": notifica al asociado y cierra la sesión."""
    cedula = session.get("member_cedula")
    proposal_ids = [int(i) for i in (session.get("proposal_ids") or []) if str(i).isdigit()]
    masked = ""
    if cedula and proposal_ids:
        masked = notify_registration_finished(cedula, proposal_ids)

    session.clear()
    if masked:
        flash(f"Registro finalizado. Enviamos la confirmación a su correo {masked}.", "success")
    else:
        flash("Registro finalizado.", "success")
    return redirect(url_for("index"))


# -----------------------------------------------------------------------------
# Administration
# -----------------------------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        ip = client_ip()
        if admin_login_limiter.is_blocked(ip):
            audit_log.warning("Login admin bloqueado por exceso de intentos ip=%s", ip)
            flash("Demasiados intentos fallidos. Espere 15 minutos e intente nuevamente.", "error")
            return render_template("admin_login.html"), 429

        password = request.form.get("password") or ""
        if _admin_password_matches(password):
            admin_login_limiter.reset(ip)
            # Sesión nueva al autenticarse (evita fijación de sesión).
            session.clear()
            session["is_admin"] = True
            session.permanent = True
            audit_log.info("Login admin exitoso ip=%s", ip)
            return redirect(url_for("admin_dashboard"))
        admin_login_limiter.register_failure(ip)
        audit_log.warning("Login admin fallido ip=%s", ip)
        flash("Contraseña incorrecta.", "error")

    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    guard = admin_guard()
    if guard:
        return guard

    stats = {
        "associates": int(fetchone("SELECT COUNT(*) AS total FROM associates")["total"]),
        "associates_with_email": int(fetchone(
            "SELECT COUNT(*) AS total FROM associates WHERE correo IS NOT NULL AND correo <> ''"
        )["total"]),
        "notifications_sent": int(fetchone(
            "SELECT COUNT(*) AS total FROM notifications WHERE estado = 'enviado'"
        )["total"]),
        "notifications_failed": int(fetchone(
            "SELECT COUNT(*) AS total FROM notifications WHERE estado = 'error'"
        )["total"]),
        "proposals": int(fetchone("SELECT COUNT(*) AS total FROM proposals")["total"]),
        "unique_submitters": int(fetchone("SELECT COUNT(DISTINCT cedula) AS total FROM proposals")["total"]),
        "repeat_submitters": int(fetchone(
            "SELECT COUNT(*) AS total FROM (SELECT cedula FROM proposals GROUP BY cedula HAVING COUNT(*) > 1) t"
        )["total"]),
    }
    recent = fetchall(
        "SELECT id, cedula, nombre, articulo, paragrafo, tipo_cambio, created_at FROM proposals ORDER BY id DESC LIMIT 10"
    )
    dashboard_proposals = fetchall(
        "SELECT id, cedula, nombre, articulo, paragrafo, tipo_cambio, created_at FROM proposals ORDER BY id DESC LIMIT 250"
    )
    structure = load_structure()
    restrictions = get_statute_restrictions()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        recent=recent,
        dashboard_proposals=dashboard_proposals,
        statute_available=STATUTE_PDF.exists(),
        statute_data=structure,
        statute_restrictions=restrictions,
        restriction_count=len(restrictions),
        structure_count=len(structure.get("items", [])),
        article_count=structure.get("article_count", 0),
        database_mode="PostgreSQL" if USE_POSTGRES else "SQLite local",
    )


def _structure_has_reference(structure, reference):
    """Comprueba que una restricción provenga realmente del Estatuto procesado."""
    fields = ("capitulo", "articulo", "paragrafo", "numeral", "literal")
    for item in structure.get("items", []):
        if all(_ref_value(item.get(field)) == _ref_value(reference.get(field)) for field in fields):
            return True
    return False


@app.route("/admin/restricciones/guardar", methods=["POST"])
def admin_save_restriction():
    guard = admin_guard()
    if guard:
        return guard

    reference = {
        "capitulo": (request.form.get("capitulo") or "").strip(),
        "articulo": (request.form.get("articulo") or "").strip(),
        "paragrafo": (request.form.get("paragrafo") or "").strip(),
        "numeral": (request.form.get("numeral") or "").strip(),
        "literal": (request.form.get("literal") or "").strip(),
    }
    mensaje = (request.form.get("mensaje") or "").strip()[:300] or "Este texto no está habilitado para cambios."

    if not any(reference.values()):
        flash("Seleccione en el Estatuto el texto que desea marcar como no editable.", "error")
        return redirect(url_for("admin_dashboard", section="restricciones"))

    structure = load_structure()
    if not _structure_has_reference(structure, reference):
        flash("La referencia seleccionada no coincide con el Estatuto vigente.", "error")
        return redirect(url_for("admin_dashboard", section="restricciones"))

    params = tuple(reference[field] for field in ("capitulo", "articulo", "paragrafo", "numeral", "literal"))
    existing = fetchone(
        """
        SELECT id FROM statute_restrictions
        WHERE active = 1
          AND COALESCE(capitulo, '') = ?
          AND COALESCE(articulo, '') = ?
          AND COALESCE(paragrafo, '') = ?
          AND COALESCE(numeral, '') = ?
          AND COALESCE(literal, '') = ?
        LIMIT 1
        """,
        params,
    )

    if existing:
        execute("UPDATE statute_restrictions SET mensaje = ? WHERE id = ?", (mensaje, existing["id"]))
        flash("La restricción ya existía; se actualizó su mensaje.", "success")
    else:
        # Si la misma referencia existía desactivada, se reactiva en lugar de duplicarla.
        inactive = fetchone(
            """
            SELECT id FROM statute_restrictions
            WHERE active = 0
              AND COALESCE(capitulo, '') = ?
              AND COALESCE(articulo, '') = ?
              AND COALESCE(paragrafo, '') = ?
              AND COALESCE(numeral, '') = ?
              AND COALESCE(literal, '') = ?
            ORDER BY id DESC LIMIT 1
            """,
            params,
        )
        if inactive:
            execute(
                "UPDATE statute_restrictions SET mensaje = ?, active = 1 WHERE id = ?",
                (mensaje, inactive["id"]),
            )
        else:
            execute(
                """
                INSERT INTO statute_restrictions
                    (capitulo, articulo, paragrafo, numeral, literal, mensaje, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                params + (mensaje,),
            )
        flash("Texto marcado como no editable.", "success")

    return redirect(url_for("admin_dashboard", section="restricciones"))


@app.route("/admin/restricciones/<int:restriction_id>/habilitar", methods=["POST"])
def admin_enable_restriction(restriction_id):
    guard = admin_guard()
    if guard:
        return guard

    row = fetchone("SELECT id FROM statute_restrictions WHERE id = ? AND active = 1", (restriction_id,))
    if not row:
        flash("La restricción ya no estaba activa.", "warning")
    else:
        execute("UPDATE statute_restrictions SET active = 0 WHERE id = ?", (restriction_id,))
        flash("El texto volvió a quedar habilitado para recibir cambios.", "success")
    return redirect(url_for("admin_dashboard", section="restricciones"))


@app.route("/admin/asociados/cargar", methods=["POST"])
def admin_load_associates():
    guard = admin_guard()
    if guard:
        return guard

    uploaded = request.files.get("base_asociados")
    mode = request.form.get("modo") or "reemplazar"
    if not uploaded or not uploaded.filename:
        flash("Seleccione el archivo de asociados.", "error")
        return redirect(url_for("admin_dashboard", section="asociados"))

    filename = secure_filename(uploaded.filename).lower()
    try:
        if filename.endswith(".csv"):
            raw_rows = _read_csv_associates(uploaded)
        elif filename.endswith((".xlsx", ".xlsm")):
            raw_rows = _read_excel_associates(uploaded)
        else:
            flash("Formato no permitido. Use CSV, XLSX o XLSM.", "error")
            return redirect(url_for("admin_dashboard", section="asociados"))
    except ValueError as exc:
        # Errores de formato redactados por la propia aplicación (columnas faltantes).
        flash(f"No fue posible leer el archivo: {exc}", "error")
        return redirect(url_for("admin_dashboard", section="asociados"))
    except Exception:
        audit_log.exception("Error leyendo la base de asociados")
        flash("No fue posible leer el archivo. Verifique que sea un CSV o Excel válido.", "error")
        return redirect(url_for("admin_dashboard", section="asociados"))

    created_at = datetime.now().isoformat(timespec="seconds")
    cleaned = {}
    ignored = 0
    with_email = 0
    invalid_email = 0
    for row in raw_rows:
        cedula = normalize_cedula(row.get("cedula"))
        nombre = str(row.get("nombre") or "").strip()
        fecha_iso = normalize_fecha_expedicion(row.get("fecha_expedicion"))
        if not cedula or not nombre or not fecha_iso:
            ignored += 1
            continue
        fecha_db = date.fromisoformat(fecha_iso) if USE_POSTGRES else fecha_iso
        raw_email = str(row.get("correo") or "").strip()
        correo = notificaciones.normalizar_correo(raw_email) or None
        if correo:
            with_email += 1
        elif raw_email:
            invalid_email += 1
        cleaned[cedula] = (cedula, nombre, fecha_db, correo, created_at)

    values = list(cleaned.values())
    if not values:
        flash("El archivo no contiene registros válidos con cédula, nombre y fecha de expedición.", "error")
        return redirect(url_for("admin_dashboard", section="asociados"))

    conn = get_db()
    cur = conn.cursor()
    try:
        if mode == "reemplazar":
            cur.execute("DELETE FROM associates")
        cur.executemany(
            _sql(
                """
                INSERT INTO associates(cedula, nombre, fecha_expedicion, correo, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cedula) DO UPDATE SET
                    nombre = excluded.nombre,
                    fecha_expedicion = excluded.fecha_expedicion,
                    correo = COALESCE(excluded.correo, associates.correo),
                    created_at = excluded.created_at
                """
            ),
            values,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    audit_log.info(
        "Base de asociados cargada modo=%s validos=%s omitidos=%s con_correo=%s correo_invalido=%s ip=%s",
        mode, len(values), ignored, with_email, invalid_email, client_ip(),
    )
    flash(
        f"Base actualizada: {len(values)} asociados válidos. Filas omitidas: {ignored}. "
        f"Con correo electrónico: {with_email}. Correos con formato inválido (no cargados): {invalid_email}. "
        "La carga se realizó de forma masiva.",
        "success",
    )
    return redirect(url_for("admin_dashboard", section="asociados"))


@app.route("/admin/estatuto/cargar", methods=["POST"])
def admin_load_statute():
    guard = admin_guard()
    if guard:
        return guard

    uploaded = request.files.get("estatuto")
    if not uploaded or not uploaded.filename:
        flash("Seleccione el archivo PDF del Estatuto.", "error")
        return redirect(url_for("admin_dashboard", section="estatuto"))

    if not secure_filename(uploaded.filename).lower().endswith(".pdf"):
        flash("El documento debe ser un archivo PDF.", "error")
        return redirect(url_for("admin_dashboard", section="estatuto"))

    # Se valida en un archivo temporal: si no es un PDF real, el Estatuto vigente no se toca.
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", dir=DATA_DIR)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        uploaded.save(tmp_path)
        with tmp_path.open("rb") as fh:
            is_pdf = fh.read(5) == b"%PDF-"
        if is_pdf:
            import pymupdf

            with pymupdf.open(tmp_path) as doc:
                is_pdf = doc.page_count > 0 and not doc.needs_pass
        if not is_pdf:
            flash("El archivo no es un PDF válido o está protegido con contraseña.", "error")
            return redirect(url_for("admin_dashboard", section="estatuto"))
        os.replace(tmp_path, STATUTE_PDF)
    except Exception:
        audit_log.exception("Error validando el PDF del Estatuto")
        flash("El archivo no es un PDF válido.", "error")
        return redirect(url_for("admin_dashboard", section="estatuto"))
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        result = process_statute_pdf(str(STATUTE_PDF), str(STATUTE_STRUCTURE), str(PAGE_IMAGES_DIR))
        audit_log.info("Estatuto reemplazado ip=%s articulos=%s", client_ip(), result.get("article_count", 0))
        flash(
            f"Estatuto actualizado correctamente: {result.get('article_count', 0)} artículos y {len(result.get('items', []))} zonas seleccionables.",
            "success",
        )
    except Exception:
        audit_log.exception("Error procesando el PDF del Estatuto")
        flash("El PDF se guardó, pero no fue posible procesarlo. Revise el registro de la aplicación.", "error")
    return redirect(url_for("admin_dashboard", section="estatuto"))


@app.route("/admin/configuracion", methods=["POST"])
def admin_settings():
    guard = admin_guard()
    if guard:
        return guard

    values = {key: (request.form.get(key) or "").strip() for key in ("admission_url", "privacy_url", "statute_title")}
    for key in ("admission_url", "privacy_url"):
        if values[key] and not safe_external_url(values[key]):
            flash("Los enlaces deben ser direcciones web completas que empiecen por https://.", "error")
            return redirect(url_for("admin_dashboard", section="configuracion"))
    if len(values["statute_title"]) > 200:
        flash("El título del Estatuto admite máximo 200 caracteres.", "error")
        return redirect(url_for("admin_dashboard", section="configuracion"))
    for key, value in values.items():
        if value:
            save_setting(key, value)
    audit_log.info("Configuración modificada ip=%s", client_ip())
    flash("Configuración guardada.", "success")
    return redirect(url_for("admin_dashboard", section="configuracion"))


@app.route("/admin/propuestas")
def admin_proposals():
    guard = admin_guard()
    if guard:
        return guard

    q = (request.args.get("q") or "").strip()
    if q:
        term = f"%{q}%"
        rows = fetchall(
            """
            SELECT id, cedula, nombre, capitulo, articulo, paragrafo, tipo_cambio, created_at
            FROM proposals
            WHERE cedula LIKE ? OR nombre LIKE ? OR articulo LIKE ? OR propuesta LIKE ?
            ORDER BY id DESC
            """,
            (term, term, term, term),
        )
    else:
        rows = fetchall(
            """
            SELECT id, cedula, nombre, capitulo, articulo, paragrafo, tipo_cambio, created_at
            FROM proposals ORDER BY id DESC
            """
        )
    return render_template("admin_proposals.html", rows=rows, q=q)


@app.route("/admin/propuestas/<int:proposal_id>")
def admin_proposal_detail(proposal_id):
    guard = admin_guard()
    if guard:
        return guard

    proposal_row = fetchone("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
    if not proposal_row:
        return "Propuesta no encontrada.", 404
    return render_template("admin_proposal_detail.html", proposal=proposal_row)


@app.route("/admin/exportar.xlsx")
def admin_export():
    guard = admin_guard()
    if guard:
        return guard

    rows = fetchall("SELECT * FROM proposals ORDER BY id DESC")
    wb = Workbook()
    ws = wb.active
    ws.title = "Propuestas"

    headers = [
        "ID", "Cédula", "Nombre", "Capítulo", "Artículo", "Parágrafo", "Numeral", "Literal",
        "Página", "Texto de referencia", "Tipo de solicitud", "Propuesta", "Justificación",
        "Modalidad", "Grupo", "Autorización datos", "Fecha"
    ]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="0B8F3A")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill

    for row in rows:
        data = dict(row)
        values = [
            data.get("id"), data.get("cedula"), data.get("nombre"), data.get("capitulo"),
            data.get("articulo"), data.get("paragrafo"), data.get("numeral"), data.get("literal"),
            data.get("pagina"), data.get("referencia_texto"), data.get("tipo_cambio"),
            data.get("propuesta"), data.get("justificacion"), data.get("modalidad_propuesta"),
            data.get("grupo_propuesta"), data.get("consentimiento_at"), data.get("created_at"),
        ]
        ws.append([excel_safe(value) for value in values])
        # Todo texto se guarda como texto literal: Excel nunca lo evalúa como fórmula.
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"

    widths = {
        "A": 8, "B": 18, "C": 30, "D": 34, "E": 12, "F": 14, "G": 12, "H": 10,
        "I": 10, "J": 65, "K": 20, "L": 70, "M": 70, "N": 22, "O": 26, "P": 22, "Q": 22,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    audit_log.info("Exportación de propuestas a Excel registros=%s ip=%s", len(rows), client_ip())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"propuestas_estatuto_{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/salir", methods=["POST"])
def admin_logout():
    session.clear()
    audit_log.info("Logout admin ip=%s", client_ip())
    flash("Sesión administrativa finalizada.", "success")
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    # El depurador de Werkzeug permite ejecutar código: solo con FLASK_DEBUG=true explícito.
    app.run(host="127.0.0.1", port=5000, debug=_env_flag("FLASK_DEBUG", False))
