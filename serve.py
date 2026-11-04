"""Servidor de producción con Waitress (compatible con Windows).

Uso: python serve.py
Variables: PORT (8000), URL_PREFIX (p. ej. /estatutos), WAITRESS_THREADS (8)
"""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SEED_DIR = Path(os.getenv("SEED_DIR") or BASE_DIR / "seed")


def _seed(source: Path, target: Path):
    """Copia los archivos iniciales al volumen solo si este está vacío."""
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        return
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"Datos iniciales copiados en {target}")


# Debe ejecutarse antes de importar la app, que procesa el Estatuto al cargar.
_seed(SEED_DIR / "data", BASE_DIR / "data")
_seed(SEED_DIR / "pages", BASE_DIR / "static" / "generated" / "pages")

from waitress import serve  # noqa: E402

from app import app  # noqa: E402

if __name__ == "__main__":
    serve(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT") or 8000),
        url_prefix=os.getenv("URL_PREFIX") or "",
        threads=int(os.getenv("WAITRESS_THREADS") or 8),
        channel_timeout=300,
        # Sin cabecera "Server: waitress" (no revelar el software del servidor).
        ident="",
    )
