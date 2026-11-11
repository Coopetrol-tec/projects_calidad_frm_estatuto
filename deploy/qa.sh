#!/usr/bin/env bash
# Despliegue versionado del entorno de QA en Linux (docker-compose.qa.yml).
#
# Uso (desde la carpeta del proyecto en el servidor):
#   sudo deploy/qa.sh desplegar v1.1.0-rc.4   # despliega un tag de git
#   sudo deploy/qa.sh revertir                # vuelve a la versión anterior
#   sudo deploy/qa.sh revertir 1.0.0          # vuelve a una versión concreta
#   sudo deploy/qa.sh estado                  # versión actual, anterior e imágenes disponibles
#
# Cada versión queda como imagen coopetrol/estatutos:<version>; revertir no reconstruye nada.
# Antes de desplegar se guarda una copia de la base PostgreSQL en backups/.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.qa.yml)
IMAGEN="coopetrol/estatutos"
URL="http://127.0.0.1:8546/estatutos/"
ACTUAL_FILE=".qa_version_actual"
ANTERIOR_FILE=".qa_version_anterior"

fallar() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# Con sudo, git se niega a operar sobre un repositorio de otro usuario ("dubious ownership"):
# las operaciones de git se ejecutan como el usuario que invocó sudo, dueño de la carpeta.
git() {
    if [[ -n "${SUDO_USER:-}" && "$(id -u)" == "0" ]]; then
        sudo -u "$SUDO_USER" git "$@"
    else
        command git "$@"
    fi
}

version_en_ejecucion() {
    local id
    id="$("${COMPOSE[@]}" ps -q app 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
        docker inspect --format '{{.Config.Image}}' "$id" | sed "s#^${IMAGEN}:##"
    elif [[ -f "$ACTUAL_FILE" ]]; then
        cat "$ACTUAL_FILE"
    fi
}

responde() {
    for _ in $(seq 1 40); do
        sleep 3
        if curl -fsS -o /dev/null --max-time 5 "$URL" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

levantar() {  # $1 = versión (tag de imagen)
    APP_VERSION="$1" "${COMPOSE[@]}" up -d --no-build app
}

respaldar_base() {  # $1 = etiqueta para el nombre del archivo
    mkdir -p backups
    local archivo
    archivo="backups/qa_$(date +%Y%m%d_%H%M%S)_$1.sql.gz"
    if "${COMPOSE[@]}" exec -T db sh -c 'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$archivo"; then
        info "Copia de la base: $archivo"
    else
        rm -f "$archivo"
        fallar "No fue posible respaldar la base; no se despliega."
    fi
}

desplegar() {
    local tag="${1:-}"
    [[ -n "$tag" ]] || fallar "Indique el tag de git a desplegar, p. ej.: deploy/qa.sh desplegar v1.1.0-rc.4"
    [[ -f .env.qa ]] || fallar "No existe .env.qa en el servidor."
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] || fallar "Hay cambios locales sin commit en el servidor."

    local previa
    previa="$(version_en_ejecucion)"
    previa="${previa:-qa}"
    local ref_previa
    ref_previa="$(git rev-parse HEAD)"

    info "Versión en ejecución: $previa"
    respaldar_base "antes_de_${tag}"

    git fetch --tags origin
    git rev-parse -q --verify "refs/tags/$tag" >/dev/null || fallar "El tag $tag no existe."
    git checkout -q --detach "$tag"

    local nueva
    nueva="$(tr -d '[:space:]' < VERSION)"
    [[ -n "$nueva" ]] || fallar "El archivo VERSION está vacío en $tag."
    info "Construyendo imagen ${IMAGEN}:${nueva}"
    if ! APP_VERSION="$nueva" "${COMPOSE[@]}" build app; then
        git checkout -q "$ref_previa"
        fallar "Falló la construcción. La versión $previa sigue en ejecución."
    fi

    if ! grep -qE '^EMAIL_HOST=.+' .env.qa; then
        echo "AVISO: .env.qa no tiene EMAIL_HOST; las notificaciones quedarán como 'deshabilitado'."
    fi

    info "Iniciando versión $nueva"
    levantar "$nueva"
    if responde; then
        [[ "$previa" != "$nueva" ]] && echo "$previa" > "$ANTERIOR_FILE"
        echo "$nueva" > "$ACTUAL_FILE"
        info "Versión $nueva desplegada y respondiendo en $URL"
        info "Si algo falla: sudo deploy/qa.sh revertir   (vuelve a $previa)"
    else
        "${COMPOSE[@]}" logs --tail 40 app || true
        echo "La versión $nueva no respondió; se vuelve automáticamente a $previa." >&2
        git checkout -q "$ref_previa"
        levantar "$previa"
        responde && info "Versión $previa restablecida." || echo "ATENCIÓN: $previa tampoco responde; revise los logs." >&2
        exit 1
    fi
}

revertir() {
    local destino="${1:-}"
    if [[ -z "$destino" ]]; then
        [[ -f "$ANTERIOR_FILE" ]] || fallar "No hay versión anterior registrada; indíquela: deploy/qa.sh revertir <version>"
        destino="$(cat "$ANTERIOR_FILE")"
    fi
    destino="${destino#v}"
    docker image inspect "${IMAGEN}:${destino}" >/dev/null 2>&1 \
        || fallar "No existe la imagen ${IMAGEN}:${destino} en este servidor. Use: deploy/qa.sh desplegar v${destino}"

    local actual
    actual="$(version_en_ejecucion)"
    info "Revirtiendo de ${actual:-?} a $destino (la base de datos no se modifica)"
    levantar "$destino"
    # Deja el código fuente alineado con la versión en ejecución, si existe el tag.
    if git rev-parse -q --verify "refs/tags/v$destino" >/dev/null; then
        git checkout -q --detach "v$destino"
    fi
    if responde; then
        [[ -n "$actual" && "$actual" != "$destino" ]] && echo "$actual" > "$ANTERIOR_FILE"
        echo "$destino" > "$ACTUAL_FILE"
        info "Versión $destino en ejecución."
    else
        "${COMPOSE[@]}" logs --tail 40 app || true
        fallar "La versión $destino no respondió."
    fi
}

estado() {
    echo "En ejecución: $(version_en_ejecucion)"
    echo "Anterior:     $(cat "$ANTERIOR_FILE" 2>/dev/null || echo '-')"
    echo "Imágenes disponibles:"
    docker image ls "$IMAGEN" --format '  {{.Tag}}\t{{.CreatedSince}}'
    echo "Copias de la base:"
    ls -1t backups/ 2>/dev/null | head -5 | sed 's/^/  /' || true
}

case "${1:-}" in
    desplegar) desplegar "${2:-}" ;;
    revertir) revertir "${2:-}" ;;
    estado) estado ;;
    *) sed -n '2,12p' "$0"; exit 1 ;;
esac
