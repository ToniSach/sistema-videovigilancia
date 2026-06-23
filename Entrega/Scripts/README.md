# Scripts auxiliares

Índice de los scripts de apoyo del proyecto (no se duplican; viven en su
ubicación original).

## Empaquetado a `.exe` de Windows — [`../../packaging/`](../../packaging/)

| Script | Función |
|--------|---------|
| [build.bat](../../packaging/build.bat) | Construye el producto final en `C:\NVR-VMS-build\dist\NVR-VMS\`. |
| [nvr_backend.spec](../../packaging/nvr_backend.spec) | Spec de PyInstaller para el backend. |
| [nvr_desktop.spec](../../packaging/nvr_desktop.spec) | Spec de PyInstaller para la app de escritorio. |
| [stage_vendor.py](../../packaging/stage_vendor.py) | Prepara binarios/recursos de terceros para el empaquetado. |
| [README.md](../../packaging/README.md) | Documentación del proceso de empaquetado. |

## Generación de documentación — [`../../scripts/`](../../scripts/)

| Script | Función |
|--------|---------|
| [md_to_docx.py](../../scripts/md_to_docx.py) | Convierte documentos Markdown a `.docx`. |
| [md_to_docx_generic.py](../../scripts/md_to_docx_generic.py) | Conversor genérico Markdown → `.docx`. |

## Base de datos — [`../BaseDatos/`](../BaseDatos/)

Scripts de creación y respaldo de la base de datos PostgreSQL (ver
[RESTAURACION.md](../BaseDatos/RESTAURACION.md)).
