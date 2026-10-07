import csv
import io
import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

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

from utils.statute_parser import process_statute_pdf

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INSTANCE_DIR = BASE_DIR / "instance"
PAGE_IMAGES_DIR = BASE_DIR / "static" / "generated" / "pages"
STATUTE_PDF = DATA_DIR / "estatuto.pdf"
STATUTE_STRUCTURE = DATA_DIR / "estatuto_structure.json"
SQLITE_PATH = INSTANCE_DIR / "app.db"

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


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or "coopetrol-local-dev-key",
    APP_NAME=os.getenv("APP_NAME") or "Propuestas de ajuste al Estatuto",
    ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD") or "",
    ADMIN_PASSWORD_HASH=os.getenv("ADMIN_PASSWORD_HASH") or "",
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
    # Ruta base de publicación (p. ej. /estatutos detrás del proxy). También fija
    # la ruta de la cookie de sesión para no mezclarla con otras aplicaciones.
    APPLICATION_ROOT=os.getenv("APPLICATION_ROOT") or "/",
    SESSION_COOKIE_NAME=os.getenv("SESSION_COOKIE_NAME") or "session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", False),
)

# Detrás de un proxy inverso (nginx/IIS) se respetan X-Forwarded-For/Proto/Host/Port.
if _env_flag("BEHIND_PROXY", False):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


app.permanent_session_lifetime = timedelta(minutes=30)
DEFAULT_SETTINGS = {
    "admission_url": "https://www.coopetrol.coop/admision-virtual/",
    "privacy_url": "https://www.coopetrol.coop/wp-content/uploads/2024/09/Manual_de_Politicas_y_Procedimientos_para_el_Tratamiento_de_Datos_Personales_WEB.pdf",
    "statute_title": "Estatuto Coopetrol 2023",
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


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "settings": get_settings(),
        "app_name": app.config["APP_NAME"],
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
    configured_hash = app.config["ADMIN_PASSWORD_HASH"]
    if configured_hash:
        return check_password_hash(configured_hash, password)
    return secrets.compare_digest(password, app.config["ADMIN_PASSWORD"])


def _associate_header_indexes(header):
    normalized = [str(v).strip().lower() if v is not None else "" for v in header]
    aliases = {
        "cedula": {"cedula", "cédula", "documento", "identificacion", "identificación", "numero_documento"},
        "nombre": {"nombre", "nombres", "nombre_completo", "nombres_apellidos", "nombres y apellidos"},
        "fecha_expedicion": {"fecha_expedicion", "fecha expedicion", "fecha de expedicion", "fecha_expedición", "fecha de expedición"},
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
            pos = idx[field]
            return row[pos] if pos < len(row) else None
        rows.append({
            "cedula": value("cedula"),
            "nombre": value("nombre"),
            "fecha_expedicion": value("fecha_expedicion"),
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
            pos = idx[field]
            return row[pos] if pos < len(row) else None
        rows.append({
            "cedula": value("cedula"),
            "nombre": value("nombre"),
            "fecha_expedicion": value("fecha_expedicion"),
        })
    return rows



def ensure_feature_schema():
    """Extiende la base existente sin borrar información."""
    conn = _connect_database()
    try:
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("ALTER TABLE associates ADD COLUMN IF NOT EXISTS nombre TEXT")
            cur.execute("ALTER TABLE associates ADD COLUMN IF NOT EXISTS fecha_expedicion DATE")
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


@app.route("/validar", methods=["POST"])
def validate_member():
    cedula = normalize_cedula(request.form.get("cedula"))
    fecha_expedicion = normalize_fecha_expedicion(request.form.get("fecha_expedicion"))
    consent = request.form.get("consent") == "on"

    if not cedula:
        flash("Ingrese su número de documento.", "error")
        return redirect(url_for("index"))
    if not fecha_expedicion:
        flash("Ingrese una fecha de expedición válida.", "error")
        return redirect(url_for("index"))
    if not consent:
        flash("Debe autorizar el tratamiento de datos personales para continuar.", "error")
        return redirect(url_for("index"))

    associate = fetchone(
        "SELECT cedula, nombre, fecha_expedicion FROM associates WHERE cedula = ?",
        (cedula,),
    )
    if not associate:
        return render_template("not_member.html", cedula=cedula)

    stored_date = normalize_fecha_expedicion(associate["fecha_expedicion"])
    if not stored_date or stored_date != fecha_expedicion:
        flash("La fecha de expedición no coincide con la información registrada.", "error")
        return redirect(url_for("index"))

    nombre = (associate["nombre"] or "").strip()
    if not nombre:
        flash("El registro del asociado no tiene nombre. Solicite al administrador actualizar la base.", "error")
        return redirect(url_for("index"))

    session["member_cedula"] = cedula
    session["member_name"] = nombre
    session["member_fecha_expedicion"] = fecha_expedicion
    session["member_consent_at"] = datetime.now().isoformat(timespec="seconds")
    session.permanent = True
    return redirect(url_for("proposal"))


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

        errors = []
        if not any([capitulo, articulo, paragrafo, numeral, literal]):
            errors.append("Seleccione en el Estatuto el apartado que desea ajustar.")
        else:
            blocked = find_blocking_restriction(capitulo, articulo, paragrafo, numeral, literal)
            if blocked:
                errors.append(blocked.get("mensaje") or "Este texto no está habilitado para cambios.")
        if not tipo_cambio:
            errors.append("Seleccione el tipo de solicitud.")
        if len(propuesta_texto) < 10:
            errors.append("Describa el ajuste propuesto con al menos 10 caracteres.")

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
                    """
                    INSERT INTO proposals (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta, consentimiento_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta_texto,
                        consentimiento_at, created_at,
                    ),
                    commit=False,
                )
                proposal_id = cur.fetchone()["id"]
                get_db().commit()
            else:
                cur = execute(
                    """
                    INSERT INTO proposals (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta, consentimiento_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cedula, nombre, capitulo, articulo, paragrafo, numeral, literal,
                        pagina, referencia_texto, tipo_cambio, propuesta_texto,
                        consentimiento_at, created_at,
                    ),
                )
                proposal_id = cur.lastrowid

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
    return render_template("thanks.html", proposal_id=proposal_id)


@app.route("/estatuto.pdf")
def statute_pdf():
    if not STATUTE_PDF.exists():
        return "No hay Estatuto cargado.", 404
    return send_file(STATUTE_PDF, mimetype="application/pdf", conditional=True)


@app.route("/salir", methods=["POST"])
def member_logout():
    session.pop("member_cedula", None)
    session.pop("member_consent_at", None)
    flash("Sesión finalizada.", "success")
    return redirect(url_for("index"))


# -----------------------------------------------------------------------------
# Administration
# -----------------------------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        if _admin_password_matches(password):
            session["is_admin"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))
        flash("Contraseña incorrecta.", "error")

    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    guard = admin_guard()
    if guard:
        return guard

    stats = {
        "associates": int(fetchone("SELECT COUNT(*) AS total FROM associates")["total"]),
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
    mensaje = (request.form.get("mensaje") or "").strip() or "Este texto no está habilitado para cambios."

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
    except Exception as exc:
        flash(f"No fue posible leer el archivo: {exc}", "error")
        return redirect(url_for("admin_dashboard", section="asociados"))

    created_at = datetime.now().isoformat(timespec="seconds")
    cleaned = {}
    ignored = 0
    for row in raw_rows:
        cedula = normalize_cedula(row.get("cedula"))
        nombre = str(row.get("nombre") or "").strip()
        fecha_iso = normalize_fecha_expedicion(row.get("fecha_expedicion"))
        if not cedula or not nombre or not fecha_iso:
            ignored += 1
            continue
        fecha_db = date.fromisoformat(fecha_iso) if USE_POSTGRES else fecha_iso
        cleaned[cedula] = (cedula, nombre, fecha_db, created_at)

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
                INSERT INTO associates(cedula, nombre, fecha_expedicion, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cedula) DO UPDATE SET
                    nombre = excluded.nombre,
                    fecha_expedicion = excluded.fecha_expedicion,
                    created_at = excluded.created_at
                """
            ),
            values,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    flash(
        f"Base actualizada: {len(values)} asociados válidos. Filas omitidas: {ignored}. "
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

    uploaded.save(STATUTE_PDF)
    try:
        result = process_statute_pdf(str(STATUTE_PDF), str(STATUTE_STRUCTURE), str(PAGE_IMAGES_DIR))
        flash(
            f"Estatuto actualizado correctamente: {result.get('article_count', 0)} artículos y {len(result.get('items', []))} zonas seleccionables.",
            "success",
        )
    except Exception as exc:
        flash(f"El PDF se guardó, pero no fue posible procesarlo: {exc}", "error")
    return redirect(url_for("admin_dashboard", section="estatuto"))


@app.route("/admin/configuracion", methods=["POST"])
def admin_settings():
    guard = admin_guard()
    if guard:
        return guard

    for key in ("admission_url", "privacy_url", "statute_title"):
        value = (request.form.get(key) or "").strip()
        if value:
            save_setting(key, value)
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
        "Página", "Texto de referencia", "Tipo de solicitud", "Propuesta", "Autorización datos", "Fecha"
    ]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="0B8F3A")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill

    for row in rows:
        ws.append([
            row["id"], row["cedula"], row["nombre"], row["capitulo"], row["articulo"], row["paragrafo"],
            row["numeral"], row["literal"], row["pagina"], row["referencia_texto"], row["tipo_cambio"],
            row["propuesta"], row["consentimiento_at"], row["created_at"],
        ])

    widths = {
        "A": 8, "B": 18, "C": 30, "D": 34, "E": 12, "F": 14, "G": 12, "H": 10,
        "I": 10, "J": 65, "K": 20, "L": 70, "M": 22, "N": 22,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"propuestas_estatuto_{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/admin/salir", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    flash("Sesión administrativa finalizada.", "success")
    return redirect(url_for("admin_login"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=_env_flag("FLASK_DEBUG", True))
