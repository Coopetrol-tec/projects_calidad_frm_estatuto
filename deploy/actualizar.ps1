# Actualiza /estatutos en el servidor sin dejarlo caído si algo falla.
# Uso (PowerShell como Administrador, desde la carpeta del proyecto):
#   Producción: powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1
#   QA:         powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Entorno qa
#
# El archivo de variables (.env.production / .env.qa) NO viene del repositorio: vive solo
# en el servidor y este script nunca lo modifica. No lo copie desde el equipo local.
param(
    [ValidateSet("produccion", "qa")]
    [string]$Entorno = "produccion",
    # Carpeta webapps de Tomcat. Vacío = no tocar Tomcat.
    [string]$Tomcat = "C:\prod\tomcat9java8\apache-tomcat-9.0.85\webapps"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if ($Entorno -eq "qa") {
    $Imagen = "coopetrol/estatutos:qa"; $Anterior = "coopetrol/estatutos:qa-anterior"
    $Contenedor = "estatutos-qa"; $Puerto = 8548; $EnvFile = ".env.qa"; $Vol = "estatuto_qa"
} else {
    $Imagen = "coopetrol/estatutos:windows"; $Anterior = "coopetrol/estatutos:anterior"
    $Contenedor = "estatutos"; $Puerto = 8547; $EnvFile = ".env.production"; $Vol = "estatuto"
}

# Ejecuta un comando nativo ignorando su salida de error (en PowerShell 5.1, "2>$null"
# con ErrorActionPreference=Stop convierte cualquier aviso en error fatal).
function Silencioso([scriptblock]$Bloque) {
    $previo = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Bloque 2>&1 | Out-Null } finally { $ErrorActionPreference = $previo }
}
function Fallar($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Valor($nombre) {
    (($envTxt | Where-Object { $_ -match "^$nombre=" }) -replace "^$nombre=", "").Trim()
}

# 1. Validar el archivo de variables antes de tocar nada
if (-not (Test-Path $EnvFile)) { Fallar "No existe $EnvFile en el servidor." }
$envTxt = Get-Content $EnvFile
if ($envTxt | Where-Object { $_ -match '^[A-Z_]+=["'']' }) {
    Fallar "$EnvFile tiene valores entre comillas; docker run --env-file las pasa literales. Quítelas."
}
if ((Valor "SECRET_KEY").Length -lt 32) { Fallar "SECRET_KEY debe tener al menos 32 caracteres aleatorios." }
if (-not (Valor "ADMIN_PASSWORD_HASH")) { Fallar "Falta ADMIN_PASSWORD_HASH en $EnvFile." }
if ((Valor "DB_ENGINE") -match '^postgres') {
    $dbUrl = Valor "DATABASE_URL"
    if (-not $dbUrl) { Fallar "$EnvFile usa PostgreSQL pero no tiene DATABASE_URL." }
    if ($dbUrl -match 'host\.docker\.internal|localhost|127\.0\.0\.1') {
        Fallar "DATABASE_URL debe usar la IP del host (p. ej. 172.26.144.1); los contenedores Windows no resuelven host.docker.internal."
    }
}

# 2. Traer el código y construir la imagen nueva (respaldando la actual)
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Fallar "git pull falló (¿cambios locales sin commit?)." }
Silencioso { docker tag $Imagen $Anterior }
docker build -f Dockerfile.windows -t $Imagen .
if ($LASTEXITCODE -ne 0) { Fallar "docker build falló. El contenedor actual sigue funcionando." }

# 3. Probar configuración y base de datos con la imagen nueva ANTES de reemplazar el contenedor
docker run --rm --isolation process --env-file $EnvFile $Imagen `
    python -c "import app; print('Configuración y base de datos OK')"
if ($LASTEXITCODE -ne 0) {
    Fallar "La imagen nueva no arranca con $EnvFile (revise el mensaje anterior). El contenedor actual sigue funcionando."
}

# 4. Reemplazar el contenedor
$volumenes = @(
    "-v", "${Vol}_data:C:\app\data",
    "-v", "${Vol}_pages:C:\app\static\generated\pages",
    "-v", "${Vol}_instance:C:\app\instance"
)
Silencioso { docker rm -f $Contenedor }
docker run -d --name $Contenedor --restart unless-stopped --isolation process `
    --env-file $EnvFile -p "${Puerto}:8000" @volumenes $Imagen | Out-Null

# 5. El puerto interno es HTTP sin TLS: solo debe alcanzarse desde el propio servidor
#    (Tomcat). Se bloquea cualquier acceso remoto; el tráfico local (127.0.0.1) sigue permitido.
$regla = "Estatutos - bloquear acceso remoto al puerto $Puerto"
if (-not (Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $regla -Direction Inbound -Protocol TCP -LocalPort $Puerto `
        -RemoteAddress "0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255" -Action Block | Out-Null
    Write-Host "Regla de firewall creada: $regla"
}

# 6. Verificar que responda
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:${Puerto}/estatutos/"
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    docker logs --tail 30 $Contenedor
    Write-Host "La versión nueva no respondió. Para volver a la anterior:" -ForegroundColor Yellow
    Write-Host "  docker rm -f $Contenedor; docker tag $Anterior $Imagen" -ForegroundColor Yellow
    Write-Host "  y ejecute de nuevo el paso 4 de este script (docker run ... $Imagen)" -ForegroundColor Yellow
    exit 1
}

# 7. Actualizar el proxy de Tomcat solo si cambió (solo producción)
if ($Entorno -eq "produccion" -and $Tomcat) {
    $war = "deploy\tomcat\estatutos.war"
    $destino = Join-Path $Tomcat "estatutos.war"
    if ((Test-Path $war) -and (-not (Test-Path $destino) -or
        (Get-FileHash $war).Hash -ne (Get-FileHash $destino).Hash)) {
        Copy-Item $war $destino -Force
        Write-Host "estatutos.war actualizado en Tomcat."
    }
}

Write-Host "Actualización completa ($Entorno): http://localhost:${Puerto}/estatutos/" -ForegroundColor Green
