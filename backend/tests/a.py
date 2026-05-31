import os
import shutil

def copiar_archivos(origen, destino):
    try:
        for raiz, dirs, files in os.walk(origen):
            # Ignorar carpetas "env" y "objects"
            dirs[:] = [d for d in dirs if d not in ("env", "objects", "tests")]

            for archivo in files:
                # Ignorar archivos .pyc
                if archivo.endswith(".pyc"):
                    continue

                # Aceptar solo .py, README y requirements
                if (archivo.endswith(".py") or 
                    archivo.lower().startswith("readme") or 
                    archivo.lower().startswith("requirements")):

                    ruta_origen = os.path.join(raiz, archivo)

                    # Crear carpeta destino si no existe
                    os.makedirs(destino, exist_ok=True)

                    ruta_destino = os.path.join(destino, archivo)

                    # Copiar archivo (sin estructura de carpetas)
                    shutil.copy2(ruta_origen, ruta_destino)
                    print(f"Copiado: {ruta_origen} -> {ruta_destino}")

    except PermissionError:
        print(f"No se pudo acceder a: {origen}")

if __name__ == "__main__":
    # Rutas fijas según lo solicitado
    origen = r"C:\Users\tonis\OneDrive\Documentos\ProyectoPrueba-TT\Proyecto-kimi\sistema-videovigilancia\backend\app"
    destino = r"C:\Users\tonis\OneDrive\Documentos\ProyectoPrueba-TT\Proyecto-kimi\parakimi-actualizado3"

    if os.path.exists(origen) and os.path.isdir(origen):
        copiar_archivos(origen, destino)
        print("\nProceso completado.")
    else:
        print("La ruta origen no existe o no es una carpeta válida.")