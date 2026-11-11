# Historial de versiones

Cada versión desplegada corresponde a un tag de git (`vX.Y.Z`) y a una imagen Docker
`coopetrol/estatutos:X.Y.Z`. Para volver a una versión anterior ver
"Versiones y reversión" en `DESPLIEGUE_DOCKER.md`.

## 1.1.0 — 2026-11-11 (producción)

Versión validada en QA (1.1.0-rc.4) que pasa a producción. Incluye todo lo de 1.1.0-rc.1 a rc.4.

- `deploy/actualizar.ps1` (producción Windows): imágenes por versión
  (`coopetrol/estatutos:windows-<versión>`), despliegue por tag (`-Version`), respaldo de la base
  antes de reemplazar el contenedor, vuelta automática si la versión nueva no responde y
  reversión manual (`-Revertir`, `-Revertir -A <versión>`).

## 1.1.0-rc.4 (QA) — 2026-11-11

- Nuevo texto del correo de confirmación (agradecimiento por participar en la reforma);
  se conserva la tabla con el detalle de las propuestas.

## 1.1.0-rc.3 (QA) — 2026-11-11

- Formulario de validación: el número de documento solo admite números, máximo 10 dígitos
  (filtro en el navegador y validación en el servidor).

## 1.1.0-rc.2 (QA) — 2026-11-11

- `deploy/qa.sh`: las operaciones de git se ejecutan como el usuario dueño del repositorio
  aunque el script corra con `sudo`. Admite servidores sin acceso a GitHub (remoto = bundle).

## 1.1.0-rc.1 (QA) — 2026-11-11

- Notificación por correo al asociado al pulsar **Finalizar registro**: un solo mensaje con
  todas las propuestas registradas en la sesión.
- Columna opcional `correo` en la base de asociados (carga CSV/Excel).
- Tabla `notifications` con el resultado de cada envío; resumen en el panel de administración.
- Archivo `VERSION`, visible en el panel de administración y en el log de arranque.
- Despliegue versionado de QA con reversión: `deploy/qa.sh`.

**Base de datos:** cambios solo aditivos (columna `associates.correo`, tabla `notifications`).
La versión 1.0.0 funciona sobre la base ya migrada, por lo que revertir no exige restaurar la base.

## 1.0.0 — 2026-11

- Versión en producción antes de las notificaciones (incluye la auditoría de seguridad).
