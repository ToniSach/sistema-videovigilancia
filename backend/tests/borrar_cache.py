import os

def borrar_pyc(origen):
    # Recorrer todas las subcarpetas y archivos
    for carpeta_actual, subcarpetas, archivos in os.walk(origen):
        # Ignorar la carpeta "env"
        if "env" in carpeta_actual.split(os.sep):
            continue

        for archivo in archivos:
            if archivo.endswith(".pyc"):
                ruta_archivo = os.path.join(carpeta_actual, archivo)
                try:
                    os.remove(ruta_archivo)
                    print(f"Borrado: {ruta_archivo}")
                except Exception as e:
                    print(f"No se pudo borrar {ruta_archivo}: {e}")

# Ejemplo de uso
carpeta_origen = r"C:\Users\tonis\OneDrive\Documentos\ProyectoPrueba-TT\Proyecto-kimi\sistema-videovigilancia"
borrar_pyc(carpeta_origen)