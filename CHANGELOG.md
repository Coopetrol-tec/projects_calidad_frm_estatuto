# Historial de versiones

Cada versión desplegada corresponde a un tag de git (`vX.Y.Z`) y a una imagen Docker
`coopetrol/estatutos:X.Y.Z`. Para volver a una versión anterior ver
"Versiones y reversión" en `DESPLIEGUE_DOCKER.md`.

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
