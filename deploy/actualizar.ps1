# Actualiza /estatutos en el servidor sin dejarlo caído si algo falla.
# Uso (PowerShell como Administrador, desde la carpeta del proyecto):
#   Producción:            powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1
#   Una versión concreta:  powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Version v1.1.0
#   Volver a la anterior:  powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Revertir
#   Volver a una versión:  powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Revertir -A 1.0.0
#   QA (Windows):          agregue -Entorno qa a cualquiera de los anteriores
#
# Cada versión queda como imagen coopetrol/estatutos:windows-<version> (o qa-win-<version>), así
# volver atrás no exige reconstruir. Antes de reemplazar el contenedor se respalda la base.
#
# El archivo de variables (.env.production / .env.qa) NO viene del repositorio: vive solo
# en el servidor y este script nunca lo modifica. No lo copie desde el equipo local.
param(
    [ValidateSet("produccion", "qa")]
    [string]$Entorno = "produccion",
    # Carpeta webapps de Tomcat. Vacío = no tocar Tomcat.
    [string]$Tomcat = "C:\prod\tomcat9java8\apache-tomcat-9.0.85\webapps",
    # Tag de git a desplegar (p. ej. v1.1.0). Vacío = git pull de la rama actual.
    [string]$Version = "",
    # Vuelve a la versión anterior (o a la indicada en -A) sin reconstruir.
    [switch]$Revertir,
    [string]$A = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Repo = "coopetrol/estatutos"
if ($Entorno -eq "qa") {
    $Imagen = "${Repo}:qa"; $Anterior = "${Repo}:qa-anterior"; $Prefijo = "qa-win"
    $Contenedor = "estatutos-qa"; $Puerto = 8548; $EnvFile = ".env.qa"; $Vol = "estatuto_qa"
} else {
    $Imagen = "${Repo}:windows"; $Anterior = "${Repo}:anterior"; $Prefijo = "windows"
    $Contenedor = "estatutos"; $Puerto = 8547; $EnvFile = ".env.production"; $Vol = "estatuto"
}
$ActualFile = ".version_${Entorno}_actual"
$AnteriorFile = ".version_${Entorno}_anterior"

# Ejecuta un comando nativo ignorando su salida de error (en PowerShell 5.1, "2>$null"
# con ErrorActionPreference=Stop convierte cualquier aviso en error fatal).
function Silencioso([scriptblock]$Bloque) {
    $previo = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Bloque 2>&1 | Out-Null } finally { $ErrorActionPreference = $previo }
}
function Fallar($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Aviso($msg) { Write-Host "AVISO: $msg" -ForegroundColor Yellow }
function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Valor($nombre) {
    (($envTxt | Where-Object { $_ -match "^$nombre=" }) -replace "^$nombre=", "").Trim()
}
function Leer($archivo) { if (Test-Path $archivo) { (Get-Content $archivo -Raw).Trim() } else { "" } }
function ImagenExiste($img) {
    $previo = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { docker image inspect $img 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) } finally { $ErrorActionPreference = $previo }
}

$volumenes = @(
    "-v", "${Vol}_data:C:\app\data",
    "-v", "${Vol}_pages:C:\app\static\generated\pages",
    "-v", "${Vol}_instance:C:\app\instance"
)
function Iniciar($img) {
    Silencioso { docker rm -f $Contenedor }
    docker run -d --name $Contenedor --restart unless-stopped --isolation process `
        --env-file $EnvFile -p "${Puerto}:8000" @volumenes $img | Out-Null
    if ($LASTEXITCODE -ne 0) { Fallar "No se pudo iniciar el contenedor con $img." }
}
function Responde {
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:${Puerto}/estatutos/"
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
    }
    return $false
}

if (-not (Test-Path $EnvFile)) { Fallar "No existe $EnvFile en el servidor." }
$envTxt = Get-Content $EnvFile

# ---------------------------------------------------------------------------
# Reversión: vuelve a una imagen ya construida, sin tocar código ni base de datos.
# ---------------------------------------------------------------------------
if ($Revertir) {
    $actual = Leer $ActualFile
    if ($A) {
        $destinoVer = $A.TrimStart("v"); $destino = "${Repo}:${Prefijo}-$destinoVer"
    } else {
        $destinoVer = Leer $AnteriorFile
        $destino = if ($destinoVer -and (ImagenExiste "${Repo}:${Prefijo}-$destinoVer")) { "${Repo}:${Prefijo}-$destinoVer" } else { $Anterior }
        if (-not $destinoVer) { $destinoVer = "anterior" }
    }
    if (-not (ImagenExiste $destino)) { Fallar "No existe la imagen $destino en este servidor." }
    Info "Revirtiendo de $(if ($actual) { $actual } else { '?' }) a $destinoVer ($destino). La base de datos no se modifica."
    Iniciar $destino
    if (-not (Responde)) { docker logs --tail 30 $Contenedor; Fallar "La versión $destinoVer no respondió." }
    docker tag $destino $Imagen | Out-Null
    if ($actual -and $actual -ne $destinoVer) { Set-Content $AnteriorFile $actual }
    Set-Content $ActualFile $destinoVer
    Write-Host "Versión $destinoVer en ejecución ($Entorno): http://localhost:${Puerto}/estatutos/" -ForegroundColor Green
    exit 0
}

# 1. Validar el archivo de variables antes de tocar nada
if ($envTxt | Where-Object { $_ -match '^[A-Z_]+=["'']' }) {
    Fallar "$EnvFile tiene valores entre comillas; docker run --env-file las pasa literales. Quítelas."
}
if ((Valor "SECRET_KEY").Length -lt 32) { Fallar "SECRET_KEY debe tener al menos 32 caracteres aleatorios." }
if (-not (Valor "ADMIN_PASSWORD_HASH")) { Fallar "Falta ADMIN_PASSWORD_HASH en $EnvFile." }
$usaPostgres = (Valor "DB_ENGINE") -match '^postgres'
if ($usaPostgres) {
    $dbUrl = Valor "DATABASE_URL"
    if (-not $dbUrl) { Fallar "$EnvFile usa PostgreSQL pero no tiene DATABASE_URL." }
    if ($dbUrl -match 'host\.docker\.internal|localhost|127\.0\.0\.1') {
        Fallar "DATABASE_URL debe usar la IP del host (p. ej. 172.26.144.1); los contenedores Windows no resuelven host.docker.internal."
    }
}
if (-not (Valor "EMAIL_HOST") -or -not (Valor "DEFAULT_FROM_EMAIL")) {
    Aviso "$EnvFile no tiene EMAIL_HOST / DEFAULT_FROM_EMAIL: no se enviarán correos al finalizar el registro."
} else {
    $smtpPuerto = if (Valor "EMAIL_PORT") { [int](Valor "EMAIL_PORT") } else { 587 }
    $previo = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $smtpOk = Test-NetConnection -ComputerName (Valor "EMAIL_HOST") -Port $smtpPuerto -InformationLevel Quiet -WarningAction SilentlyContinue
    $ErrorActionPreference = $previo
    if (-not $smtpOk) { Aviso "El servidor no alcanza el SMTP en el puerto $smtpPuerto; los correos quedarán con estado 'error'." }
}

$previa = Leer $ActualFile
if (-not $previa) { $previa = "anterior" }

# 2. Traer el código
if ($Version) {
    git fetch --tags origin
    if ($LASTEXITCODE -ne 0) { Fallar "git fetch falló." }
    git checkout -q --detach $Version
    if ($LASTEXITCODE -ne 0) { Fallar "No se pudo cambiar al tag $Version (¿existe? ¿cambios locales sin commit?)." }
} else {
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Fallar "git pull falló (¿cambios locales sin commit, o el código está en un tag? use -Version)." }
}
$ver = if (Test-Path VERSION) { (Get-Content VERSION -Raw).Trim() } else { "" }
if (-not $ver) { $ver = Get-Date -Format "yyyyMMdd-HHmm" }
$ImagenVersion = "${Repo}:${Prefijo}-$ver"
Info "Versión en ejecución: $previa  ->  versión nueva: $ver"

# 3. Construir la imagen nueva (respaldando la actual)
if (ImagenExiste $Imagen) { docker tag $Imagen $Anterior | Out-Null }
docker build -f Dockerfile.windows -t $ImagenVersion -t $Imagen .
if ($LASTEXITCODE -ne 0) {
    if (ImagenExiste $Anterior) { docker tag $Anterior $Imagen | Out-Null }
    Fallar "docker build falló. El contenedor actual sigue funcionando."
}

# 4. Probar configuración y base de datos con la imagen nueva ANTES de reemplazar el contenedor
docker run --rm --isolation process --env-file $EnvFile $ImagenVersion `
    python -c "import app; print('Configuracion y base de datos OK')"
if ($LASTEXITCODE -ne 0) {
    docker tag $Anterior $Imagen | Out-Null
    Fallar "La imagen nueva no arranca con $EnvFile (revise el mensaje anterior). El contenedor actual sigue funcionando."
}

# 5. Respaldar la base antes de reemplazar el contenedor
$fecha = Get-Date -Format "yyyyMMdd_HHmmss"
if ($usaPostgres) {
    if (Get-Command pg_dump -ErrorAction SilentlyContinue) {
        New-Item -ItemType Directory -Force backups | Out-Null
        $respaldo = "backups\${Entorno}_${fecha}_antes_de_$ver.sql"
        pg_dump --clean --if-exists --dbname=$dbUrl -f $respaldo
        if ($LASTEXITCODE -ne 0) { docker tag $Anterior $Imagen | Out-Null; Fallar "pg_dump falló; no se despliega." }
        Info "Respaldo de la base: $respaldo"
    } else {
        Aviso "pg_dump no está instalado en el servidor: haga un respaldo manual de PostgreSQL antes de continuar."
        $resp = Read-Host "¿Continuar sin respaldo automático? (s/N)"
        if ($resp -notmatch '^[sS]') { docker tag $Anterior $Imagen | Out-Null; Fallar "Despliegue cancelado." }
    }
} else {
    # SQLite: copia consistente dentro del mismo volumen (C:\app\instance).
    $respaldo = "respaldo_${fecha}_antes_de_$ver.db"
    $py = "import sqlite3; s=sqlite3.connect(r'C:\app\instance\app.db'); d=sqlite3.connect(r'C:\app\instance\$respaldo'); s.backup(d); d.close(); print('ok')"
    $previo = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $salida = docker exec $Contenedor python -c $py 2>&1
    $ErrorActionPreference = $previo
    if ($LASTEXITCODE -eq 0) {
        Info "Respaldo de la base: volumen ${Vol}_instance\$respaldo"
    } else {
        Aviso "No se pudo respaldar la base SQLite ($salida)."
        $resp = Read-Host "¿Continuar sin respaldo? (s/N)"
        if ($resp -notmatch '^[sS]') { docker tag $Anterior $Imagen | Out-Null; Fallar "Despliegue cancelado." }
    }
}

# 6. Reemplazar el contenedor
Iniciar $ImagenVersion

# 7. El puerto interno es HTTP sin TLS: solo debe alcanzarse desde el propio servidor
#    (Tomcat). Se bloquea cualquier acceso remoto; el tráfico local (127.0.0.1) sigue permitido.
$regla = "Estatutos - bloquear acceso remoto al puerto $Puerto"
if (-not (Get-NetFirewallRule -DisplayName $regla -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $regla -Direction Inbound -Protocol TCP -LocalPort $Puerto `
        -RemoteAddress "0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255" -Action Block | Out-Null
    Write-Host "Regla de firewall creada: $regla"
}

# 8. Verificar que responda; si no, volver automáticamente a la versión anterior
if (-not (Responde)) {
    docker logs --tail 30 $Contenedor
    Aviso "La versión $ver no respondió; se vuelve automáticamente a la anterior ($previa)."
    if (ImagenExiste $Anterior) {
        Iniciar $Anterior
        docker tag $Anterior $Imagen | Out-Null
        if (Responde) { Write-Host "Versión anterior restablecida." -ForegroundColor Yellow }
        else { Write-Host "ATENCIÓN: la versión anterior tampoco responde; revise docker logs $Contenedor." -ForegroundColor Red }
    }
    exit 1
}
if ($previa -ne $ver) { Set-Content $AnteriorFile $previa }
Set-Content $ActualFile $ver
# La imagen previa también queda con su versión, para poder volver a ella por número.
if ($previa -ne "anterior" -and (ImagenExiste $Anterior) -and -not (ImagenExiste "${Repo}:${Prefijo}-$previa")) {
    docker tag $Anterior "${Repo}:${Prefijo}-$previa" | Out-Null
}

# 9. Actualizar el proxy de Tomcat solo si cambió (solo producción)
if ($Entorno -eq "produccion" -and $Tomcat) {
    $war = "deploy\tomcat\estatutos.war"
    $destino = Join-Path $Tomcat "estatutos.war"
    if ((Test-Path $war) -and (-not (Test-Path $destino) -or
        (Get-FileHash $war).Hash -ne (Get-FileHash $destino).Hash)) {
        Copy-Item $war $destino -Force
        Write-Host "estatutos.war actualizado en Tomcat."
    }
}

Write-Host "Versión $ver desplegada ($Entorno): http://localhost:${Puerto}/estatutos/" -ForegroundColor Green
Write-Host "Si algo falla: powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1$(if ($Entorno -eq 'qa') { ' -Entorno qa' }) -Revertir   (vuelve a $previa)"
