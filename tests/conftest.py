"""Configuración de pruebas: base SQLite temporal y servidor SMTP en memoria.

Las variables se fijan ANTES de importar app.py, que lee la configuración al importarse.
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_TMP = Path(tempfile.mkdtemp(prefix="estatutos-pruebas-"))
SMTP_PORT = _puerto_libre()
os.environ.update({
    "DB_ENGINE": "sqlite",
    "SQLITE_PATH": str(_TMP / "app.db"),
    "SECRET_KEY": "k" * 48,
    "ADMIN_PASSWORD": "admin-pruebas",
    "APP_ENV": "development",
    "EMAIL_HOST": "127.0.0.1",
    "EMAIL_PORT": str(SMTP_PORT),
    "EMAIL_USE_TLS": "false",
    "EMAIL_USE_SSL": "false",
    "EMAIL_HOST_USER": "",
    "EMAIL_HOST_PASSWORD": "",
    "DEFAULT_FROM_EMAIL": "COOPETROL <no-responder@coopetrol.coop>",
})

import app as appmod  # noqa: E402
import notificaciones  # noqa: E402

CSRF = "t" * 48


class _Buzon:
    def __init__(self):
        self.mensajes = []

    async def handle_DATA(self, server, session, envelope):
        self.mensajes.append(envelope)
        return "250 OK"


@pytest.fixture(scope="session")
def buzon():
    from aiosmtpd.controller import Controller

    handler = _Buzon()
    ctrl = Controller(handler, hostname="127.0.0.1", port=SMTP_PORT)
    ctrl.start()
    yield handler
    ctrl.stop()


@pytest.fixture(autouse=True)
def reintentos_rapidos(monkeypatch):
    monkeypatch.setattr(notificaciones, "REINTENTOS", (0, 0.1, 0.1))


@pytest.fixture
def app():
    appmod.app.config["TESTING"] = True
    return appmod


@pytest.fixture
def db(app):
    import sqlite3

    conn = sqlite3.connect(app.SQLITE_PATH)
    yield conn
    conn.close()


def nuevo_cliente(app, admin=False):
    c = app.app.test_client()
    with c.session_transaction() as s:
        s["_csrf_token"] = CSRF
        if admin:
            s["is_admin"] = True
    return c


@pytest.fixture
def cliente(app):
    return nuevo_cliente(app)


@pytest.fixture
def admin(app):
    return nuevo_cliente(app, admin=True)
