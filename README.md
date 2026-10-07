# Formulario de recomendaciones al Estatuto - COOPETROL

Aplicación web local en **Python + Flask** para que los asociados de COOPETROL consulten el Estatuto y registren recomendaciones de modificación, adición o eliminación.

Esta versión está preparada para probarse de inmediato en Visual Studio Code con **SQLite**, sin necesidad de instalar ni configurar PostgreSQL.

## Funcionalidades incluidas

### Vista del asociado
- Validación de asociado por número de cédula.
- Base de cédulas cargable por el administrador desde CSV o Excel.
- Si la cédula no está en la base, informa que no figura como asociado y muestra el enlace a admisión en línea.
- Autorización de tratamiento de datos personales con enlace configurable.
- Estatuto 2023 incluido en el proyecto.
- Visor interactivo del Estatuto dentro del formulario.
- El asociado puede **hacer clic directamente sobre un apartado del documento** y la aplicación precarga automáticamente la referencia detectada.
- Búsqueda por artículo o palabras del Estatuto.
- Formulario simplificado con solamente:
  - nombres y apellidos;
  - tipo de solicitud;
  - propuesta de ajuste.
- Permite múltiples recomendaciones por el mismo asociado.
- Informa cuántas recomendaciones ya tiene registradas la cédula.
- Muestra la nota institucional de análisis por parte del Consejo de Administración.

### Vista administrativa
- Panel independiente en `/admin`.
- Dashboard liviano con **menú lateral izquierdo**.
- Secciones independientes para:
  - Resumen;
  - Base de asociados;
  - Estatuto;
  - Configuración;
  - Propuestas;
  - Exportación a Excel.
- Indicadores de asociados, propuestas, participantes y usuarios con múltiples envíos.
- Carga o reemplazo de la base de asociados.
- Reemplazo del PDF del Estatuto y reprocesamiento de las zonas interactivas.
- Consulta y búsqueda de propuestas.
- Exportación del consolidado a Excel.

## 1. Instalación en Visual Studio Code

Abra la carpeta `Formulario_estatuto_coopetrol` en Visual Studio Code.

En VS Code seleccione **Terminal > New Terminal**.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Si ya tiene `.venv`, solo active el entorno y ejecute:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

La aplicación quedará disponible en:

```text
http://127.0.0.1:5000
```

Administración:

```text
http://127.0.0.1:5000/admin
```

## 2. Contraseña inicial del administrador

En `.env.example` se incluye para pruebas locales:

```env
ADMIN_PASSWORD_HASH=<hash generado con Werkzeug>
```

Después de copiar `.env.example` a `.env`, puede cambiar esa contraseña en el archivo `.env`.

## 3. Probar la validación de asociados

El proyecto incluye:

```text
plantillas/base_asociados_ejemplo.csv
```

Contiene estas cédulas de prueba:

```text
100000001
100000002
100000003
```

Para cargarlas:

1. Entre a `/admin`.
2. Use la contraseña configurada.
3. Abra **Base de asociados** en el menú izquierdo.
4. Cargue `plantillas/base_asociados_ejemplo.csv`.
5. Seleccione **Reemplazar la base vigente**.
6. Regrese al formulario público y valide con `100000001`.

## 4. Estatuto interactivo

El proyecto ya incluye:

```text
data/estatuto.pdf
```

También se incluyen las imágenes y la estructura generadas para el visor interactivo.

El procesamiento actual detecta los **100 artículos** del Estatuto y genera zonas seleccionables para capítulos, artículos, parágrafos, numerales y literales cuando la diagramación del PDF permite identificarlos.

Cuando el administrador reemplaza el Estatuto, la aplicación procesa nuevamente el PDF con PyMuPDF.

## 5. Base de datos

### Para las pruebas actuales: SQLite

Mantenga en `.env`:

```env
DB_ENGINE=sqlite
```

La aplicación creará automáticamente:

```text
instance/app.db
```

No necesita instalar PostgreSQL para probar el sistema.

### PostgreSQL más adelante

La aplicación ya deja preparado el cambio de motor. Cuando quiera retomar la migración, puede instalar el driver:

```powershell
pip install "psycopg[binary]>=3.2,<4"
```

Y configurar por ejemplo:

```env
DB_ENGINE=postgres
DATABASE_URL=postgresql://usuario:clave@localhost:5432/formulario_estatuto
```

Mientras `DB_ENGINE=sqlite`, la aplicación utilizará SQLite aunque exista una `DATABASE_URL` antigua en el archivo `.env`.

## 6. Formato de la base de asociados

Se aceptan:

```text
.csv
.xlsx
.xlsm
```

El formato recomendado es una única columna:

| cedula |
|---|
| 123456789 |
| 987654321 |

También se admite un archivo con una única columna aunque no tenga encabezado.

La aplicación elimina puntos, espacios y guiones de las cédulas y evita duplicados.

## 7. Archivos principales

```text
Formulario_estatuto_coopetrol/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
├── data/
│   ├── estatuto.pdf
│   └── estatuto_structure.json
├── instance/
├── plantillas/
│   └── base_asociados_ejemplo.csv
├── static/
│   ├── css/
│   │   └── styles.css
│   ├── generated/
│   │   └── pages/
│   ├── img/
│   │   └── coopetrol-logo.png
│   └── js/
│       ├── admin.js
│       └── proposal.js
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── proposal.html
│   ├── not_member.html
│   ├── thanks.html
│   ├── admin_login.html
│   ├── admin_dashboard.html
│   ├── admin_proposals.html
│   └── admin_proposal_detail.html
└── utils/
    ├── __init__.py
    └── statute_parser.py
```

## 8. Si aparece un error con PyMuPDF

La aplicación usa ahora:

```python
import pymupdf
```

No utiliza `import fitz`, evitando el aviso de API obsoleta que aparece en versiones recientes de PyMuPDF.

Compruebe la instalación con:

```powershell
python -c "import pymupdf; print(pymupdf.__version__)"
```

## 9. Despliegue en servidor (Docker)

Para publicar en `https://sistemas.coopetrol.coop:8546/estatutos` consulte
[DESPLIEGUE_DOCKER.md](DESPLIEGUE_DOCKER.md).

## 10. Recomendaciones antes de publicar

Para una prueba local SQLite es suficiente. Antes de publicar en producción se recomienda:

- cambiar `SECRET_KEY`;
- cambiar `ADMIN_PASSWORD`;
- usar HTTPS;
- migrar a PostgreSQL;
- proteger el panel administrativo con controles adicionales;
- establecer una política institucional de respaldo y retención de datos.
