"""
Paquete `storage` — Integridad del almacenamiento de grabaciones (#11, fase de
mantenimiento).

Contiene `consistency_checker`, el verificador periódico que reconcilia la tabla
`recordings` (BD) con los archivos en disco: detecta y limpia registros sin
archivo (huérfanos de BD) y archivos sin registro (huérfanos de FS).

Es COMPLEMENTARIO al paquete hermano `recording`:
  - recording/storage_manager.py borra por CUOTA (LRU por antigüedad).
  - storage/consistency_checker.py borra por INCONSISTENCIA (FS↔BD).

La instancia global `consistency_checker` la arranca y detiene main.py.
"""
