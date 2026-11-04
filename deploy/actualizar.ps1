# Actualiza /estatutos en el servidor sin dejarlo caído si algo falla.
# Uso (PowerShell como Administrador, desde la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1
#
# .env.production NO viene del repositorio: vive solo en el servidor y este
# script nunca lo modifica. No lo copie desde el equipo local.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$Imagen     = "coopetrol/estatutos:windows"
$Anterior   = "coopetrol/estatutos:anterior"
$Contenedor = "estatutos"
$EnvFile    = ".env.production"
$Tomcat     = "C:\prod\tomcat9java8\apache-tomcat-9.0.85\webapps"

function Fallar($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# 1. Validar .env.production antes de tocar nada
if (-not (Test-Path $EnvFile)) { Fallar "No existe $EnvFile en el servidor." }
$envTxt = Get-Content $EnvFile
$dbUrl  = ($envTxt | Where-Object { $_ -match '^DATABASE_URL=' }) -replace '^DATABASE_URL=', ''
if (-not $dbUrl) { Fallar "$EnvFile no tiene DATABASE_URL." }
if ($dbUrl -match 'host\.docker\.internal|localhost|127\.0\.0\.1') {
    Fallar "DATABASE_URL debe usar 172.26.144.1 (los contenedores Windows no resuelven host.docker.internal)."
}
if ($envTxt | Where-Object { $_ -match '^[A-Z_]+=["'']' }) {
    Fallar "$EnvFile tiene valores entre comillas; docker run --env-file las pasa literales. Quítelas."
}

# 2. Traer el código y construir la imagen nueva (respaldando la actual)
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Fallar "git pull falló (¿cambios locales sin commit?)." }
docker tag $Imagen $Anterior 2>$null
docker build -f Dockerfile.windows -t $Imagen .
if ($LASTEXITCODE -ne 0) { Fallar "docker build falló. El contenedor actual sigue funcionando." }

# 3. Probar la conexión a la base con la imagen nueva ANTES de reemplazar el contenedor
docker run --rm --isolation process --env-file $EnvFile $Imagen `
    python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=10).close(); print('Conexion a PostgreSQL OK')"
if ($LASTEXITCODE -ne 0) {
    Fallar "La imagen nueva no conecta a PostgreSQL con $EnvFile (revise host y contraseña). El contenedor actual sigue funcionando."
}

# 4. Reemplazar el contenedor
docker rm -f $Contenedor | Out-Null
docker run -d --name $Contenedor --restart unless-stopped --isolation process `
    --env-file $EnvFile -p 8547:8000 `
    -v estatuto_data:C:\app\data `
    -v estatuto_pages:C:\app\static\generated\pages `
    -v estatuto_instance:C:\app\instance `
    $Imagen | Out-Null

# 5. Verificar que responda
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 http://localhost:8547/estatutos/
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    docker logs --tail 30 $Contenedor
    Write-Host "La versión nueva no respondió. Para volver a la anterior:" -ForegroundColor Yellow
    Write-Host "  docker rm -f $Contenedor; docker tag $Anterior $Imagen" -ForegroundColor Yellow
    Write-Host "  docker run -d --name $Contenedor --restart unless-stopped --isolation process --env-file $EnvFile -p 8547:8000 -v estatuto_data:C:\app\data -v estatuto_pages:C:\app\static\generated\pages -v estatuto_instance:C:\app\instance $Imagen" -ForegroundColor Yellow
    exit 1
}

# 6. Actualizar el proxy de Tomcat solo si cambió
$war = "deploy\tomcat\estatutos.war"
$destino = Join-Path $Tomcat "estatutos.war"
if ((Test-Path $war) -and (-not (Test-Path $destino) -or
    (Get-FileHash $war).Hash -ne (Get-FileHash $destino).Hash)) {
    Copy-Item $war $destino -Force
    Write-Host "estatutos.war actualizado en Tomcat."
}

Write-Host "Actualización completa: https://sistemas.coopetrol.coop:8546/estatutos/" -ForegroundColor Green
