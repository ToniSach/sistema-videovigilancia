import os
import shutil

def copiar_archivos(origen, destino):
    try:
        for raiz, dirs, files in os.walk(origen):
            # Ignorar carpetas "env", "objects" y "desktop_app"
            dirs[:] = [d for d in dirs if d not in ("env", "objects")]

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
    origen = input("Introduce la ruta de la carpeta origen: ").strip()
    destino = input("Introduce la ruta de la carpeta destino: ").strip()

    if os.path.exists(origen) and os.path.isdir(origen):
        copiar_archivos(origen, destino)
        print("\nProceso completado.")
    else:
        print("La ruta origen no existe o no es una carpeta válida.")