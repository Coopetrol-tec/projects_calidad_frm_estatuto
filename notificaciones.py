"""Notificaciones por correo electrónico al asociado.

La configuración se toma de las variables EMAIL_* del entorno (.env / .env.qa /
.env.production). El envío se hace en un hilo aparte para que el cierre del
registro nunca quede esperando al servidor de correo.
"""

import logging
import os
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html import escape

log = logging.getLogger("estatutos.notificaciones")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_CORREO = 254
REINTENTOS = (0, 10, 60)  # segundos de espera antes de cada intento
SMTP_TIMEOUT = 20


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "si", "sí", "on"}


def configuracion():
    port_raw = _env("EMAIL_PORT")
    use_ssl = _env_bool("EMAIL_USE_SSL")
    return {
        "host": _env("EMAIL_HOST"),
        "port": int(port_raw) if port_raw.isdigit() else (465 if use_ssl else 587),
        "user": _env("EMAIL_HOST_USER"),
        "password": _env("EMAIL_HOST_PASSWORD"),
        "use_tls": _env_bool("EMAIL_USE_TLS"),
        "use_ssl": use_ssl,
        "from": _env("DEFAULT_FROM_EMAIL") or _env("EMAIL_HOST_USER"),
    }


def correo_configurado() -> bool:
    cfg = configuracion()
    return bool(cfg["host"] and cfg["from"])


def normalizar_correo(value) -> str:
    """Devuelve el correo en minúsculas si es válido; cadena vacía si no."""
    text = str(value or "").strip().strip("<>").strip().lower()
    if not text or len(text) > MAX_CORREO or not EMAIL_RE.match(text):
        return ""
    return text


def enmascarar_correo(correo: str) -> str:
    """j***@dominio.com: se muestra en pantalla y en registros sin exponer el dato completo."""
    if not correo or "@" not in correo:
        return ""
    usuario, dominio = correo.split("@", 1)
    return f"{usuario[:1]}***@{dominio}"


def _referencia(p) -> str:
    partes = []
    for campo, etiqueta in (
        ("capitulo", "Capítulo"),
        ("articulo", "Artículo"),
        ("paragrafo", "Parágrafo"),
        ("numeral", "Numeral"),
        ("literal", "Literal"),
    ):
        valor = (p.get(campo) or "").strip()
        if valor:
            partes.append(valor if valor.lower().startswith(etiqueta.lower()) else f"{etiqueta} {valor}")
    return " · ".join(partes) or "—"


def _fecha(valor: str) -> str:
    # created_at se guarda como ISO 'YYYY-MM-DDTHH:MM:SS'.
    texto = (valor or "").replace("T", " ")
    if re.match(r"^\d{4}-\d{2}-\d{2}", texto):
        return f"{texto[8:10]}/{texto[5:7]}/{texto[0:4]}{texto[10:16]}"
    return texto


def construir_mensaje(destino: str, nombre: str, propuestas, app_name: str) -> EmailMessage:
    cfg = configuracion()
    total = len(propuestas)
    plural = total != 1
    asunto = (
        "Confirmación: registro exitoso de su"
        + (" reforma" if not plural else "s reformas")
        + " al Estatuto - COOPETROL"
    )
    agradecimiento = (
        "Reciba nuestro más sincero agradecimiento por participar en el proceso de reforma "
        "al Estatuto de COOPETROL. Su interés, compromiso y aportes contribuyen al "
        "crecimiento y fortalecimiento de nuestra cooperativa."
    )
    confirmacion = (
        "Le confirmamos que sus propuestas fueron registradas exitosamente."
        if plural else
        "Le confirmamos que su propuesta fue registrada exitosamente."
    )
    cierre = (
        "Gracias por ser protagonista de este proceso y ayudarnos a construir juntos "
        "una mejor COOPETROL."
    )
    pie = "Este es un mensaje automático; por favor no responda a este correo."

    # Texto plano
    lineas = [f"Apreciado(a) {nombre}:", "", agradecimiento, "", confirmacion, ""]
    for p in propuestas:
        lineas.append(
            f"- Registro #{p['id']} | {_referencia(p)} | {p.get('tipo_cambio') or ''} | {_fecha(p.get('created_at'))}"
        )
    lineas += ["", cierre, "", "Cordialmente,", "COOPETROL", "", pie]
    texto = "\n".join(lineas)

    # HTML (todos los datos se escapan)
    filas = "".join(
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>#{escape(str(p['id']))}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{escape(_referencia(p))}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb'>{escape(p.get('tipo_cambio') or '')}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #e5e7eb;white-space:nowrap'>{escape(_fecha(p.get('created_at')))}</td>"
        "</tr>"
        for p in propuestas
    )
    html = f"""<!doctype html>
<html lang="es"><body style="margin:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#1f2937">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden">
<tr><td style="background:#0B8F3A;color:#ffffff;padding:20px 24px;font-size:18px;font-weight:bold">{escape(app_name)}</td></tr>
<tr><td style="padding:24px">
<p style="margin:0 0 12px">Apreciado(a) <strong>{escape(nombre)}</strong>:</p>
<p style="margin:0 0 12px">{escape(agradecimiento)}</p>
<p style="margin:0 0 16px">{escape(confirmacion)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:14px">
<tr style="background:#f0fdf4;text-align:left">
<th style="padding:8px">Registro</th><th style="padding:8px">Apartado</th><th style="padding:8px">Tipo</th><th style="padding:8px">Fecha</th>
</tr>
{filas}
</table>
<p style="margin:16px 0 0;padding:12px;background:#f9fafb;border-left:4px solid #0B8F3A">{escape(cierre)}</p>
<p style="margin:20px 0 0">Cordialmente,<br><strong>COOPETROL</strong></p>
</td></tr>
<tr><td style="padding:12px 24px;background:#f9fafb;color:#6b7280;font-size:12px">{escape(pie)}</td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = cfg["from"]
    msg["To"] = destino
    msg["Date"] = formatdate(localtime=True)
    dominio = parseaddr(cfg["from"])[1].split("@")[-1] or None
    msg["Message-ID"] = make_msgid(domain=dominio)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    return msg


def enviar(msg: EmailMessage) -> None:
    cfg = configuracion()
    contexto = ssl.create_default_context()
    if cfg["use_ssl"]:
        servidor = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT, context=contexto)
    else:
        servidor = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
    with servidor:
        if cfg["use_tls"] and not cfg["use_ssl"]:
            servidor.starttls(context=contexto)
        if cfg["user"] and cfg["password"]:
            servidor.login(cfg["user"], cfg["password"])
        servidor.send_message(msg)


def enviar_en_segundo_plano(msg: EmailMessage, al_terminar) -> None:
    """Envía con reintentos en un hilo daemon.

    al_terminar(estado, intentos, error) se invoca al final con estado
    'enviado' o 'error' para registrar el resultado.
    """

    def tarea():
        ultimo_error = ""
        for intento, espera in enumerate(REINTENTOS, start=1):
            if espera:
                time.sleep(espera)
            try:
                enviar(msg)
                al_terminar("enviado", intento, "")
                return
            except Exception as exc:  # noqa: BLE001 - se registra y se reintenta
                ultimo_error = f"{type(exc).__name__}: {exc}"[:500]
                log.warning(
                    "Fallo enviando notificación intento=%s destino=%s error=%s",
                    intento, enmascarar_correo(str(msg["To"])), ultimo_error,
                )
        al_terminar("error", len(REINTENTOS), ultimo_error)

    threading.Thread(target=tarea, name="notificacion-correo", daemon=True).start()
