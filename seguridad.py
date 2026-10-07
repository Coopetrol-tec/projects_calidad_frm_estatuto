#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DEFAULT = Path("/home/calidad/Frm_Estatuto")

class PatchError(RuntimeError):
    pass

def ensure_timedelta_import(text: str) -> str:
    m = re.search(r"^from\s+datetime\s+import\s+([^\n]+)$", text, re.M)
    if m:
        imports = [x.strip() for x in m.group(1).split(",")]
        if "datetime" not in imports:
            imports.append("datetime")
        if "timedelta" not in imports:
            imports.append("timedelta")
        new_line = "from datetime import " + ", ".join(imports)
        return text[:m.start()] + new_line + text[m.end():]
    if re.search(r"^import\s+datetime\s*$", text, re.M):
        return "from datetime import timedelta\n" + text
    raise PatchError("No encontré la importación de datetime para agregar timedelta.")

def ensure_session_config(text: str) -> str:
    settings = [
        ("SESSION_COOKIE_HTTPONLY", "True"),
        ("SESSION_COOKIE_SAMESITE", '\"Lax\"'),
        ("SESSION_COOKIE_SECURE", "False"),
    ]
    missing = []
    for key, value in settings:
        if not re.search(rf"^\s*{re.escape(key)}\s*=", text, re.M):
            missing.append(f"    {key}={value},")
    if not missing:
        return text

    m = re.search(r"app\.config\.update\(\s*\n(?P<body>.*?)(?P<close>^\s*\)\s*$)", text, re.S | re.M)
    if not m:
        raise PatchError("No encontré el bloque app.config.update(...).")
    body = m.group("body")
    if body and not body.endswith("\n"):
        body += "\n"
    new_body = body + "\n".join(missing) + "\n"
    return text[:m.start("body")] + new_body + text[m.end("body"):]

def ensure_lifetime(text: str) -> str:
    if re.search(r"^\s*app\.permanent_session_lifetime\s*=", text, re.M):
        return text
    m = re.search(r"app\.config\.update\(\s*\n.*?^\s*\)\s*$", text, re.S | re.M)
    if not m:
        raise PatchError("No encontré dónde termina app.config.update(...).")
    return text[:m.end()] + "\n\napp.permanent_session_lifetime = timedelta(minutes=30)" + text[m.end():]

def ensure_member_session_permanent(text: str) -> str:
    m_func = re.search(r"(?ms)^def\s+validate_member\(\):(?P<body>.*?)(?=^def\s+|^@app\.route|\Z)", text)
    if not m_func:
        raise PatchError("No encontré la función validate_member().")
    body = m_func.group("body")
    if "session.permanent = True" in body:
        return text

    m = re.search(r'(?m)^(?P<indent>\s*)session\["member_consent_at"\]\s*=.*$', body)
    if not m:
        m = re.search(r'(?m)^(?P<indent>\s*)session\["member_cedula"\]\s*=.*$', body)
    if not m:
        raise PatchError("No encontré dónde se establece la sesión del asociado.")

    body_new = body[:m.end()] + f'\n{m.group("indent")}session.permanent = True' + body[m.end():]
    return text[:m_func.start("body")] + body_new + text[m_func.end("body"):]

def ensure_admin_session_permanent(text: str) -> str:
    funcs = list(re.finditer(
        r"(?ms)^def\s+(?P<name>[A-Za-z0-9_]+)\([^)]*\):(?P<body>.*?)(?=^def\s+|^@app\.route|\Z)",
        text
    ))
    for f in funcs:
        body = f.group("body")
        if ("ADMIN_PASSWORD" in body or "ADMIN_PASSWORD_HASH" in body):
            if "session.permanent = True" in body:
                return text
            m = re.search(r'(?m)^(?P<indent>\s*)session\[[\'\"][^\'\"]*admin[^\'\"]*[\'\"]\]\s*=\s*True\s*$', body)
            if m:
                body_new = body[:m.end()] + f'\n{m.group("indent")}session.permanent = True' + body[m.end():]
                return text[:f.start("body")] + body_new + text[f.end("body"):]

    m = re.search(r'(?m)^(?P<indent>\s*)session\[[\'\"][^\'\"]*admin[^\'\"]*[\'\"]\]\s*=\s*True\s*$', text)
    if m:
        tail = text[m.end():m.end()+120]
        if "session.permanent = True" in tail:
            return text
        return text[:m.end()] + f'\n{m.group("indent")}session.permanent = True' + text[m.end():]

    raise PatchError("No encontré la asignación de sesión del administrador.")

def validate_result(text: str) -> None:
    required = [
        "SESSION_COOKIE_HTTPONLY=True",
        'SESSION_COOKIE_SAMESITE=\"Lax\"',
        "SESSION_COOKIE_SECURE=False",
        "app.permanent_session_lifetime = timedelta(minutes=30)",
    ]
    for item in required:
        if item not in text:
            raise PatchError(f"Validación incompleta: falta {item}")
    if text.count("session.permanent = True") < 2:
        raise PatchError("Esperaba session.permanent = True para asociado y administrador.")

def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica endurecimiento de sesiones y cookies a Frm_Estatuto.")
    parser.add_argument("--project", default=str(PROJECT_DEFAULT))
    parser.add_argument("--restart-service", action="store_true")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    app_path = project / "app.py"

    print(f"Proyecto: {project}")
    if not app_path.exists():
        print(f"ERROR: No existe {app_path}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = project / f"backup_seguridad_sesiones_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_app = backup_dir / "app.py"
    shutil.copy2(app_path, backup_app)
    print(f"Respaldo: {backup_dir}")

    original = app_path.read_text(encoding="utf-8")

    try:
        updated = ensure_timedelta_import(original)
        updated = ensure_session_config(updated)
        updated = ensure_lifetime(updated)
        updated = ensure_member_session_permanent(updated)
        updated = ensure_admin_session_permanent(updated)
        validate_result(updated)

        tmp = app_path.with_suffix(".py.tmp_security")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(app_path)

        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(app_path)],
            cwd=str(project),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PatchError(result.stderr.strip() or "app.py no compiló correctamente.")

        print("  OK  app.py")
        print("  OK  SESSION_COOKIE_HTTPONLY=True")
        print('  OK  SESSION_COOKIE_SAMESITE="Lax"')
        print("  OK  SESSION_COOKIE_SECURE=False")
        print("  OK  Sesión máxima: 30 minutos")
        print("  OK  Sesión permanente para asociado y administrador")
        print("\nActualización aplicada correctamente.")

        if args.restart_service:
            print("\nReiniciando frm-estatuto.service...")
            subprocess.run(["sudo", "systemctl", "restart", "frm-estatuto.service"], check=True)
            subprocess.run(["sudo", "systemctl", "status", "frm-estatuto.service", "--no-pager"], check=False)
        else:
            print("\nEjecute ahora:")
            print("  sudo systemctl restart frm-estatuto.service")
            print("  sudo systemctl status frm-estatuto.service --no-pager")
        return 0

    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("Restaurando app.py desde el respaldo...")
        shutil.copy2(backup_app, app_path)
        print("La actualización fue revertida automáticamente.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
