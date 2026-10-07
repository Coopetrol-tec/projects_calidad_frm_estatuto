FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Bogota

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios; las carpetas con datos se montan como volúmenes.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/instance /app/data /app/static/generated/pages \
    && chown -R appuser:appuser /app/instance /app/data /app/static/generated
USER appuser

# Gunicorn recorta este prefijo de la URL, así Flask genera enlaces bajo /estatutos.
ENV SCRIPT_NAME=/estatutos \
    APPLICATION_ROOT=/estatutos \
    BEHIND_PROXY=true \
    FLASK_DEBUG=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8000' + os.environ.get('SCRIPT_NAME','') + '/', timeout=4)" || exit 1

# --preload: crea/migra el esquema una sola vez antes de lanzar los workers.
# timeout amplio: reprocesar el PDF del Estatuto puede tardar.
CMD ["gunicorn", "app:app", "--preload", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--forwarded-allow-ips", "*"]
