import io
import time

import pytest

from conftest import CSRF, nuevo_cliente


def esperar(cond, segundos=10):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def cargar_base(admin, contenido, modo="reemplazar"):
    return admin.post("/admin/asociados/cargar", data={
        "_csrf_token": CSRF, "modo": modo,
        "base_asociados": (io.BytesIO(contenido.encode("utf-8")), "base.csv"),
    }, content_type="multipart/form-data", follow_redirects=True)


@pytest.fixture
def base(admin):
    cargar_base(admin,
                "cedula;nombre;fecha_expedicion;Correo Electrónico\n"
                "111;Ana Pérez;15/06/2015;Ana.Perez@Ejemplo.com\n"
                "222;Luis Gómez;01/01/2010;correo-malo\n"
                "333;Sin Correo;02/02/2012;\n"
                "1020304050;Diez Digitos;15/06/2015;diez@ejemplo.com\n")


@pytest.fixture
def referencia(app):
    with app.app.app_context():
        return next(
            i for i in app.load_structure()["items"]
            if i.get("articulo") and not app.find_blocking_restriction(
                i.get("capitulo") or "", i.get("articulo") or "", i.get("paragrafo") or "",
                i.get("numeral") or "", i.get("literal") or "")
        )


def ingresar(c, cedula, fecha):
    r = c.post("/validar", data={"_csrf_token": CSRF, "cedula": cedula,
                                 "fecha_expedicion": fecha, "consent": "on"},
               follow_redirects=True)
    # Al validar, la app renueva la sesión (evita fijación) y descarta el token CSRF.
    with c.session_transaction() as s:
        s["_csrf_token"] = CSRF
    return r


def proponer(c, item, tipo="Modificación"):
    r = c.post("/propuesta", data={
        "_csrf_token": CSRF,
        "capitulo": item.get("capitulo") or "", "articulo": item.get("articulo") or "",
        "paragrafo": item.get("paragrafo") or "", "numeral": item.get("numeral") or "",
        "literal": item.get("literal") or "", "pagina": str(item.get("page") or ""),
        "referencia_texto": "texto", "tipo_cambio": tipo,
        "propuesta": "Propuesta de prueba <b>con html</b>", "justificacion": "Justificación",
        "modalidad_propuesta": "Propuesta individual",
    }, headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["proposal_id"]


def ultima_notificacion(db):
    return db.execute(
        "SELECT estado, intentos, proposal_ids, destino FROM notifications ORDER BY id DESC"
    ).fetchone()


# --- Carga de la base de asociados -------------------------------------------

def test_carga_base_con_correo(admin, db):
    r = cargar_base(admin, "cedula;nombre;fecha_expedicion;correo\n"
                           "111;Ana;15/06/2015;Ana@Ejemplo.com\n222;Luis;01/01/2010;malo\n")
    html = r.get_data(as_text=True)
    assert "Con correo electrónico: 1" in html
    assert "formato inválido (no cargados): 1" in html
    assert db.execute("SELECT correo FROM associates WHERE cedula='111'").fetchone()[0] == "ana@ejemplo.com"
    assert db.execute("SELECT correo FROM associates WHERE cedula='222'").fetchone()[0] is None


def test_modo_agregar_sin_columna_conserva_correo(admin, base, db):
    cargar_base(admin, "cedula,nombre,fecha_expedicion\n111,Ana Pérez,15/06/2015\n", modo="agregar")
    assert db.execute("SELECT correo FROM associates WHERE cedula='111'").fetchone()[0] == "ana.perez@ejemplo.com"


def test_panel_muestra_version_y_correos(admin, base, app):
    html = admin.get("/admin/dashboard").get_data(as_text=True)
    assert f"v{app.APP_VERSION}" in html
    assert "con correo" in html


# --- Validación del número de documento --------------------------------------

@pytest.mark.parametrize("valor,ingresa", [
    ("1020304050", True),
    (" 1020304050 ", True),
    ("12345678901", False),
    ("1.020.304.050", False),
    ("10203O4050", False),
    ("-1020304050", False),
    ("１２３", False),
])
def test_validacion_documento(app, base, valor, ingresa):
    c = nuevo_cliente(app)
    html = ingresar(c, valor, "2015-06-15").get_data(as_text=True)
    with c.session_transaction() as s:
        assert bool(s.get("member_cedula")) is ingresa
    if not ingresa:
        assert "solo admite números" in html


def test_formulario_tiene_pattern(cliente):
    html = cliente.get("/").get_data(as_text=True)
    assert 'pattern="[0-9]{1,10}"' in html


# --- Notificación al finalizar el registro -----------------------------------

def test_finalizar_envia_un_correo_consolidado(app, base, referencia, buzon, db):
    antes = len(buzon.mensajes)
    c = nuevo_cliente(app)
    ingresar(c, "111", "2015-06-15")
    p1 = proponer(c, referencia)
    p2 = proponer(c, referencia, "Adición")
    html = c.post("/finalizar", data={"_csrf_token": CSRF}, follow_redirects=True).get_data(as_text=True)
    assert "a***@ejemplo.com" in html
    assert esperar(lambda: len(buzon.mensajes) == antes + 1)

    import email
    from email import policy
    env = buzon.mensajes[-1]
    assert env.rcpt_tos == ["ana.perez@ejemplo.com"]
    msg = email.message_from_bytes(env.content, policy=policy.default)
    texto = msg.get_body(("plain",)).get_content()
    html_correo = msg.get_body(("html",)).get_content()
    assert "reformas" in msg["Subject"]
    assert "Apreciado(a) Ana Pérez" in texto
    assert "sus propuestas fueron registradas exitosamente" in texto
    assert f"#{p1}" in texto and f"#{p2}" in texto
    assert "<b>con html</b>" not in html_correo
    assert esperar(lambda: ultima_notificacion(db)[0] == "enviado")
    with c.session_transaction() as s:
        assert "member_cedula" not in s


def test_doble_clic_no_duplica(app, base, referencia, buzon):
    c = nuevo_cliente(app)
    ingresar(c, "111", "2015-06-15")
    proponer(c, referencia)
    with c.session_transaction() as s:
        copia = dict(s)
    antes = len(buzon.mensajes)
    c.post("/finalizar", data={"_csrf_token": CSRF})
    with c.session_transaction() as s:
        s.update(copia)
    c.post("/finalizar", data={"_csrf_token": CSRF})
    esperar(lambda: len(buzon.mensajes) > antes, 3)
    time.sleep(0.5)
    assert len(buzon.mensajes) == antes + 1


def test_sin_correo_finaliza_igual(app, base, referencia, db):
    c = nuevo_cliente(app)
    ingresar(c, "333", "2012-02-02")
    proponer(c, referencia)
    html = c.post("/finalizar", data={"_csrf_token": CSRF}, follow_redirects=True).get_data(as_text=True)
    assert "Registro finalizado." in html
    assert ultima_notificacion(db)[0] == "sin_destino"


def test_salir_y_finalizar_sin_propuestas_no_notifican(app, base, referencia, db):
    total = db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    c = nuevo_cliente(app)
    ingresar(c, "111", "2015-06-15")
    c.post("/finalizar", data={"_csrf_token": CSRF})
    c2 = nuevo_cliente(app)
    ingresar(c2, "111", "2015-06-15")
    proponer(c2, referencia)
    c2.post("/salir", data={"_csrf_token": CSRF})
    assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == total


def test_smtp_caido_no_bloquea_y_registra_error(app, base, referencia, db, monkeypatch):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto_cerrado = s.getsockname()[1]
    monkeypatch.setenv("EMAIL_PORT", str(puerto_cerrado))
    c = nuevo_cliente(app)
    ingresar(c, "111", "2015-06-15")
    proponer(c, referencia)
    inicio = time.time()
    r = c.post("/finalizar", data={"_csrf_token": CSRF})
    assert r.status_code == 302 and time.time() - inicio < 3
    assert esperar(lambda: ultima_notificacion(db)[0] == "error", 30)
    assert ultima_notificacion(db)[1] == 3


def test_sin_configuracion_smtp_queda_deshabilitado(app, base, referencia, db, monkeypatch):
    monkeypatch.setenv("EMAIL_HOST", "")
    c = nuevo_cliente(app)
    ingresar(c, "111", "2015-06-15")
    proponer(c, referencia)
    c.post("/finalizar", data={"_csrf_token": CSRF})
    assert ultima_notificacion(db)[0] == "deshabilitado"


# --- Mensaje del correo -------------------------------------------------------

def test_mensaje_singular_y_escapado():
    import notificaciones
    msg = notificaciones.construir_mensaje(
        "x@ejemplo.com", "<script>Ana</script>",
        [{"id": 1, "articulo": "25", "tipo_cambio": "Modificación", "created_at": "2026-11-11T10:30:00"}],
        "Propuestas")
    texto = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "su propuesta fue registrada exitosamente" in texto
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Comisión de reforma de estatutos" in texto
