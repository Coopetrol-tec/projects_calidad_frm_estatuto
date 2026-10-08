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

## Producción actual: contenedor Windows + Tomcat

Así está desplegado hoy. El servidor es Windows Server 2025 en VMware, con Docker Engine en modo
contenedores Windows, sin Docker Compose ni WSL. El puerto 8546 lo atiende **Tomcat 9**
(`C:\prod\tomcat9java8\apache-tomcat-9.0.85`), que comparte con otros sistemas.

```text
Internet ─HTTPS:8546─► Tomcat 9 ─/estatutos/*─► estatutos.war (proxy) ─► 127.0.0.1:8547 ─► contenedor "estatutos" (Waitress)
```

| Elemento | Detalle |
|---|---|
| Código | `C:\Users\Administrador\Documents\estatutos\Frm_Estatuto` (clonado por SSH con la deploy key `.ssh\id_estatutos`) |
| Imagen | `coopetrol/estatutos:windows`, construida con `Dockerfile.windows` |
| Contenedor | `estatutos`, puerto local **8547** (no se abre en el firewall) |
| Base de datos | SQLite en el volumen `estatuto_instance` |
| Variables | `.env.production`, solo en el servidor. El hash de la contraseña va **sin comillas** |
| Proxy | `deploy/tomcat/estatutos.war` copiado en `webapps` de Tomcat |

### Instalación (ya realizada)

```powershell
Copy-Item .env.production.windows.example .env.production   # y completar valores
docker build -f Dockerfile.windows -t coopetrol/estatutos:windows .
docker run -d --name estatutos --restart unless-stopped --isolation process `
  --env-file .env.production -p 8547:8000 `
  -v estatuto_data:C:\app\data `
  -v estatuto_pages:C:\app\static\generated\pages `
  -v estatuto_instance:C:\app\instance `
  coopetrol/estatutos:windows
Copy-Item deploy\tomcat\estatutos.war C:\prod\tomcat9java8\apache-tomcat-9.0.85\webapps\
```

### Actualizar la aplicación

```powershell
cd C:\Users\Administrador\Documents\estatutos\Frm_Estatuto
powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1
```

El script valida `.env.production`, hace `git pull`, construye la imagen, prueba la conexión a
PostgreSQL con la imagen nueva y solo entonces reemplaza el contenedor. Si algo falla antes de ese
punto, el contenedor actual sigue funcionando.

`.env.production` vive **solo en el servidor** (no está en git): no lo copie desde el equipo local.
`DATABASE_URL` debe usar `172.26.144.1`, no `host.docker.internal`, y los valores van sin comillas.
`docker restart` no vuelve a leer `.env.production`; después de editarlo hay que recrear el contenedor.

Los datos (base, PDF del Estatuto e imágenes) están en los volúmenes `estatuto_*` y no se pierden.

### Respaldo de la base de datos

```powershell
New-Item -ItemType Directory -Force C:\respaldos | Out-Null
docker cp estatutos:C:\app\instance\app.db "C:\respaldos\estatutos_$(Get-Date -Format yyyyMMdd_HHmm).db"
```

### Diagnóstico

| Síntoma | Revisar |
|---|---|
| La URL da error 500/502 de Tomcat | `docker ps --filter name=estatutos` y `docker logs estatutos` |
| `localhost:8547/estatutos/` no responde | `docker logs estatutos` |
| `/estatutos` da 404 de Tomcat | Que exista `webapps\estatutos.war`, y revisar `logs\catalina.*.log` |
| Retirar la publicación | Borrar `webapps\estatutos.war`; Tomcat la retira sola |

## Alternativa: usar IIS como proxy en lugar de nginx

Si el puerto 8546 ya lo administra IIS con el certificado instalado, elimine el servicio `proxy`,
publique `app` en `127.0.0.1:8000` (`ports: ["127.0.0.1:8000:8000"]`) y cree en IIS (URL Rewrite +
ARR) una regla que reenvíe `estatutos/(.*)` a `http://127.0.0.1:8000/estatutos/{R:1}`, conservando
el host y agregando el encabezado `X-Forwarded-Proto: https`.
