# Despliegue con Docker en Windows Server

URL final: **https://sistemas.coopetrol.coop:8546/estatutos**

> **Producción actual:** ver la sección *Producción actual: contenedor Windows + Tomcat*. El resto
> de este documento describe la variante con contenedores Linux (nginx + PostgreSQL).

## Arquitectura

```text
Internet ──HTTPS:8546──► proxy (nginx, TLS) ──/estatutos/──► app (Flask + Gunicorn :8000) ──► db (PostgreSQL 16)
```

| Servicio | Imagen | Datos persistentes (volúmenes) |
|---|---|---|
| `proxy` | nginx:1.27-alpine | certificados en `./certs` (solo lectura) |
| `app` | se construye con `Dockerfile` | `estatuto_data` (PDF + estructura), `estatuto_pages` (imágenes), `estatuto_instance` |
| `db` | postgres:16-alpine | `pgdata` |

Solo se expone el puerto **8546**; la app y la base de datos no son accesibles desde fuera.

## 1. Requisito: Docker con contenedores Linux

Las imágenes son **Linux**. En Windows Server, Docker Engine (Mirantis/Moby) ejecuta por defecto
contenedores **Windows**, así que se necesita una de estas opciones:

- **Windows Server 2022/2025 con WSL2** + Docker Engine instalado dentro de la distribución Linux
  (Ubuntu). Recomendado: sin licencias adicionales.
- **Docker Desktop** (requiere suscripción paga en organizaciones de más de 250 empleados o
  ingresos > USD 10M, y Microsoft no lo soporta oficialmente en Windows Server).
- Alternativa: una VM Linux (Hyper-V) dentro del servidor con Docker.

Compruebe con `docker info` que aparezca `OSType: linux`.

## 2. Copiar el proyecto al servidor

Copie la carpeta completa (sin `.venv`, `.env` ni `instance/app.db`), por ejemplo a
`C:\apps\Frm_Estatuto` (o a `/opt/estatutos` si usa WSL2).

## 3. Certificado SSL

Coloque el certificado de `sistemas.coopetrol.coop` en la carpeta `certs/`:

```text
certs/fullchain.pem   (certificado + cadena intermedia)
certs/privkey.pem     (clave privada sin contraseña)
```

Si el certificado está en `.pfx` (lo habitual en Windows/IIS), conviértalo con OpenSSL:

```bash
openssl pkcs12 -in certificado.pfx -clcerts -nokeys -out certs/fullchain.pem
openssl pkcs12 -in certificado.pfx -cacerts -nokeys -chain >> certs/fullchain.pem
openssl pkcs12 -in certificado.pfx -nocerts -nodes -out certs/privkey.pem
```

## 4. Variables de entorno

```powershell
Copy-Item .env.production.example .env.production
```

Edite `.env.production` y defina como mínimo:

- `SECRET_KEY` → `python -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_PASSWORD_HASH` → entre **comillas simples** (el hash contiene `$`).
- `POSTGRES_PASSWORD` y la **misma** clave dentro de `DATABASE_URL` (host `db`).

## 5. Construir y levantar

```powershell
docker compose build
docker compose up -d
docker compose ps          # los tres servicios deben quedar "healthy"/"running"
docker compose logs -f app
```

Abra https://sistemas.coopetrol.coop:8546/estatutos y el panel en `/estatutos/admin`.

## 6. Firewall de Windows

```powershell
New-NetFirewallRule -DisplayName "Estatutos HTTPS 8546" -Direction Inbound -Protocol TCP -LocalPort 8546 -Action Allow
```

Verifique que ningún otro servicio (p. ej. IIS) esté usando el puerto 8546:
`netstat -ano | findstr :8546`.

## 7. Migrar datos existentes (opcional)

Si ya hay datos en el PostgreSQL actual:

```bash
# En el servidor anterior
pg_dump -U estatuto_app -d formulario_estatuto -Fc -f estatuto.dump

# En el nuevo servidor
docker compose cp estatuto.dump db:/tmp/estatuto.dump
docker compose exec db pg_restore -U estatuto_app -d formulario_estatuto --clean --if-exists /tmp/estatuto.dump
docker compose restart app
```

Si en cambio se quiere conservar SQLite, ponga `DB_ENGINE=sqlite` en `.env.production`, elimine
el servicio `db` y copie el archivo: `docker compose cp instance/app.db app:/app/instance/app.db`.

## 8. Operación

| Tarea | Comando |
|---|---|
| Actualizar código | `docker compose build app; docker compose up -d app` |
| Ver logs | `docker compose logs -f app proxy` |
| Reiniciar | `docker compose restart` |
| Respaldo de BD | `docker compose exec db pg_dump -U estatuto_app -Fc formulario_estatuto > respaldo.dump` |
| Renovar certificado | reemplazar archivos en `certs/` y `docker compose restart proxy` |

Los contenedores tienen `restart: unless-stopped`: vuelven a arrancar solos cuando reinicia el
servidor, siempre que Docker esté configurado para iniciar con el sistema.

> Nota: al actualizar la imagen, el PDF y las imágenes del Estatuto **no** se sobrescriben, porque
> viven en volúmenes. Para cambiar el Estatuto use el panel administrativo.

## Variante para contenedores Windows (servidor actual)

El servidor de producción (Windows Server 2025 en VMware, Docker Engine con `OSType: windows`)
no puede ejecutar las imágenes Linux, y el puerto 8546 ya lo atiende otro servicio con el
certificado de `sistemas.coopetrol.coop`. Para ese caso se usa:

| Archivo | Uso |
|---|---|
| `Dockerfile.windows` | Imagen `python:3.12-windowsservercore-ltsc2025` con Waitress |
| `docker-compose.windows.yml` | Un solo servicio, publicado en el puerto local **8547** |
| `.env.production.windows.example` | Variables (SQLite por defecto) |
| `serve.py` | Arranca Waitress con el prefijo `/estatutos` |

```powershell
Copy-Item .env.production.windows.example .env.production
notepad .env.production
docker compose -f docker-compose.windows.yml up -d --build
docker compose -f docker-compose.windows.yml ps
Invoke-WebRequest http://localhost:8547/estatutos/ -UseBasicParsing | Select-Object StatusCode
```

Luego, en el proxy que ya escucha en el 8546, se agrega una regla que reenvíe `/estatutos/` a
`http://127.0.0.1:8547/estatutos/` conservando el host y con `X-Forwarded-Proto: https`.
El puerto 8547 **no** se abre en el firewall.

Actualizar: `git pull` y `docker compose -f docker-compose.windows.yml up -d --build`.

## Alternativa: usar IIS como proxy en lugar de nginx

Si el puerto 8546 ya lo administra IIS con el certificado instalado, elimine el servicio `proxy`,
publique `app` en `127.0.0.1:8000` (`ports: ["127.0.0.1:8000:8000"]`) y cree en IIS (URL Rewrite +
ARR) una regla que reenvíe `estatutos/(.*)` a `http://127.0.0.1:8000/estatutos/{R:1}`, conservando
el host y agregando el encabezado `X-Forwarded-Proto: https`.
