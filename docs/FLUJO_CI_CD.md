# Flujo de trabajo y CI/CD

## Ramas

Solo existen dos ramas:

| Rama | Es | Se despliega en | Quién escribe |
|---|---|---|---|
| `main` | La versión estable, la única en producción | Producción (`https://sistemas.coopetrol.coop:8546/estatutos/`), con aprobación | Solo `Coopetrol-tec`, fusionando un PR desde `develop` |
| `develop` | La versión en pruebas | QA (`http://192.168.252.59:8546/estatutos/`), automático | El equipo, con push directo |

No se pueden crear otras ramas (lo impide una regla del repositorio).

```text
  equipo ── push ──► develop ──► Pruebas ──► Desplegar en QA (automático)
                        │
                        └── PR develop → main  (solo Coopetrol-tec puede fusionarlo)
                                 │
  main ◄── merge ────────────────┘
    └──► Pruebas ──► [Aprobación de Coopetrol-tec] ──► Desplegar en producción ──► tag vX.Y.Z
```

## Trabajo diario

1. Trabaje sobre `develop`: `git switch develop && git pull`.
2. Haga commit y `git push`. GitHub Actions ejecuta las pruebas y, si pasan, despliega en QA.
   El avance se ve en la pestaña **Actions** del repositorio.
3. Si las pruebas fallan, no se despliega nada: corrija y vuelva a hacer push.

## Publicar una versión en producción

1. En `develop`, suba el número en `VERSION` (p. ej. `1.1.2` → `1.2.0`) y describa los cambios
   en `CHANGELOG.md`. Haga push y valide en QA.
2. En GitHub, abra un PR **de `develop` a `main`**. El CI comprueba que:
   - las pruebas pasen;
   - el PR venga de `develop`;
   - la versión de `VERSION` no esté publicada todavía.
3. `Coopetrol-tec` fusiona el PR con **Create a merge commit**.
4. El job **Desplegar en producción** queda esperando: `Coopetrol-tec` entra a la ejecución en
   **Actions** y pulsa **Review deployments → produccion → Approve and deploy**.
5. Al terminar bien, el CI crea el tag `vX.Y.Z`.

Cada versión queda como imagen Docker propia en el servidor, con respaldo de la base previo.

## Volver a una versión anterior

- **Producción** (en el servidor, PowerShell como Administrador):
  `powershell -ExecutionPolicy Bypass -File deploy\actualizar.ps1 -Revertir`
  (o `-Revertir -A 1.1.1` para una versión concreta).
- **QA**: `cd ~/estatutos-qa && sudo deploy/qa.sh revertir`.
- Para que el código vuelva atrás de forma permanente, revierta el commit en `develop`
  (`git revert`) y publique una versión nueva.

---

# Configuración inicial (una sola vez)

La cuenta `Coopetrol-tec` es personal y está en plan **Free**; como el repositorio es **público**,
las reglas de ramas y los entornos con aprobación están disponibles sin costo. Todo se hace con la
cuenta `Coopetrol-tec` en `https://github.com/Coopetrol-tec/projects_calidad_frm_estatuto`.

> **Orden importante:** configure los pasos 1 a 4 **antes** de activar los despliegues (paso 7).

## 1. Opciones generales — *Settings → General*

- **Default branch**: `main`.
- En **Pull Requests**: deje marcado solo **Allow merge commits** (desmarque *squash* y *rebase*;
  si `main` y `develop` divergen en historia, cada PR traería conflictos).
- Marque **Automatically delete head branches**.

## 2. Acciones — *Settings → Actions → General*

- **Fork pull request workflows from outside collaborators**: *Require approval for all external
  contributors*. El repositorio es público y hay runners en servidores internos.
- **Workflow permissions**: *Read repository contents and packages permissions*.

## 3. Entornos — *Settings → Environments*

| Entorno | Deployment branches | Required reviewers |
|---|---|---|
| `qa` | *Selected branches*: `develop` | ninguno |
| `produccion` | *Selected branches*: `main` | `Coopetrol-tec` (deje **sin** marcar *Prevent self-review*) |

## 4. Reglas de ramas — *Settings → Rules → Rulesets → New branch ruleset*

Cree tres rulesets con **Enforcement status: Active**.

**a) `main protegida`**
- *Target branches*: **Include default branch**.
- *Bypass list*: **Repository admin**, modo **For pull requests only**.
- Reglas: **Restrict updates**, **Restrict deletions**, **Block force pushes**,
  **Require a pull request before merging** (0 aprobaciones) y **Require status checks to pass**
  con `Pruebas` y `Versión nueva`.

  Resultado: nadie hace push directo a `main`. Solo el administrador (`Coopetrol-tec`) puede
  fusionar PR. Los colaboradores pueden abrir PR, pero no fusionarlos. Si un check falla, GitHub
  ofrece "bypass": no lo use salvo emergencia.

**b) `develop protegida`**
- *Target branches*: *Include by pattern* `develop`.
- Reglas: **Restrict deletions**, **Block force pushes**.

**c) `solo main y develop`**
- *Target branches*: *Include all branches*; *Exclude by pattern* `main` y `develop`.
- *Bypass list*: **Repository admin** (por si alguna vez se necesita una rama de emergencia).
- Reglas: **Restrict creations**.

## 5. Colaboradores — *Settings → Collaborators*

Invite a cada integrante (p. ej. Cristian) con rol **Write**. Con las reglas anteriores pueden
hacer push a `develop` y abrir PR, pero no modificar `main`.

## 6. Runners self-hosted

Los servidores están en la red interna; el runner solo abre conexiones salientes hacia GitHub.
El token de registro se obtiene en *Settings → Actions → Runners → New self-hosted runner* (vence
en una hora). Use los comandos de descarga que muestra esa página (versión actual del runner) y
reemplace la configuración por la de abajo.

### 6.1 QA (Linux, servidor 192.168.252.59, usuario `pqrs`)

```bash
mkdir -p ~/actions-runner-estatutos && cd ~/actions-runner-estatutos
# Descargar y descomprimir el runner con los comandos de la página de GitHub, luego:
./config.sh --url https://github.com/Coopetrol-tec/projects_calidad_frm_estatuto \
  --token <TOKEN> --name estatutos-qa --labels estatutos-qa --work _work --unattended
sudo ./svc.sh install pqrs && sudo ./svc.sh start

# Permiso para desplegar sin contraseña, limitado a un único script propiedad de root:
cd ~/estatutos-qa
sudo install -o root -g root -m 0755 deploy/qa/estatutos-qa-deploy /usr/local/sbin/estatutos-qa-deploy
echo 'pqrs ALL=(root) NOPASSWD: /usr/local/sbin/estatutos-qa-deploy' | sudo tee /etc/sudoers.d/estatutos-qa
sudo chmod 0440 /etc/sudoers.d/estatutos-qa && sudo visudo -c
```

Si `deploy/qa/estatutos-qa-deploy` cambia en el repositorio, repita el `install` (el que se
ejecuta es la copia en `/usr/local/sbin`, que el runner no puede modificar).

### 6.2 Producción (Windows, PowerShell como Administrador)

```powershell
mkdir C:\actions-runner-estatutos; cd C:\actions-runner-estatutos
# Descargar y descomprimir el runner con los comandos de la página de GitHub, luego:
.\config.cmd --url https://github.com/Coopetrol-tec/projects_calidad_frm_estatuto `
  --token <TOKEN> --name estatutos-prod --labels estatutos-prod --work _work `
  --runasservice --windowslogonaccount ".\Administrador" --windowslogonpassword "<clave>"
```

El servicio debe correr con la cuenta **Administrador**: es la que tiene permisos de Docker y la
deploy key (`.ssh\id_estatutos`) con la que el clon del servidor hace `git fetch`.

## 7. Variables y activación — *Settings → Secrets and variables → Actions → Variables*

| Variable | Valor |
|---|---|
| `PROD_APP_DIR` | `C:\Users\Administrador\Documents\estatutos\Frm_Estatuto` |
| `CICD_QA_ACTIVO` | `true`: activa el despliegue automático a QA (cuando el runner de QA esté instalado) |
| `CICD_PRODUCCION_ACTIVO` | `true`: activa el despliegue a producción (**solo** después del paso 3) |

Mientras una variable no valga `true`, el job correspondiente se omite y el CI solo ejecuta pruebas.

## Seguridad

- Los jobs que usan runners internos solo se ejecutan en `push` a `main`/`develop` del propio
  repositorio, nunca en PR ni desde forks.
- Nunca suba `.env`, `.env.qa` ni `.env.production`: viven solo en cada servidor (`.gitignore`).
- El repositorio es público: no incluya datos de asociados, contraseñas ni direcciones internas
  sensibles en el código, los issues o los logs del CI.
