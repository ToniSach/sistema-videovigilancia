import sys
from pathlib import Path
import json
import cv2
import logging

# =========================
# PATH SETUP
# =========================
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent.absolute()
project_dir = backend_dir.parent.absolute()

if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

logging.basicConfig(level=logging.INFO, format='%(message)s')

from backend.app.cameras.onvif_discovery import ONVIFDiscovery


# =========================
# DISCOVERY
# =========================
def discover_cameras():
    print("=" * 60)
    print("🔍 DESCUBRIMIENTO DE CÁMARAS")
    print("=" * 60)

    discovery = ONVIFDiscovery()

    cameras = discovery.discover(timeout=5)

    if not cameras:
        print("❌ No se encontraron cámaras automáticamente")
        return []

    print(f"✅ {len(cameras)} cámara(s) encontrada(s):\n")

    for i, cam in enumerate(cameras, 1):
        print(f"[{i}] {cam.get('name', 'Desconocida')}")
        print(f"   IP: {cam.get('ip_address', 'N/A')}")
        print(f"   RTSP: {cam.get('rtsp_url', 'N/A')}")
        print(f"   ONVIF: {cam.get('onvif_url', 'N/A')}")
        print()

    # Guardar JSON
    output_file = current_dir / "discovered_cameras.json"
    with open(output_file, "w", encoding='utf-8') as f:
        json.dump(cameras, f, indent=2, ensure_ascii=False)

    print(f"💾 Guardado en: {output_file}\n")

    return cameras


# =========================
# SELECCIÓN
# =========================
def select_camera(cameras):
    if not cameras:
        return None

    if len(cameras) == 1:
        print("Usando cámara detectada automáticamente\n")
        return cameras[0]

    while True:
        try:
            idx = int(input(f"Selecciona cámara (1-{len(cameras)}): "))
            return cameras[idx - 1]
        except:
            print("❌ Opción inválida")


# =========================
# STREAM TEST
# =========================
def run_stream(rtsp_url):
    print("=" * 60)
    print("📺 TEST DE STREAM")
    print("=" * 60)

    print(f"Conectando a:\n{rtsp_url}\n")
    print("Q = salir | S = snapshot\n")

    cap = cv2.VideoCapture(rtsp_url)

    if not cap.isOpened():
        print("❌ No se pudo abrir el stream")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"✅ Stream: {width}x{height} @ {fps}fps\n")

    frame_count = 0

    while True:
        ret, frame = cap.read()

        if not ret:
            print("⚠️ Reconectando...")
            cap.release()
            cap = cv2.VideoCapture(rtsp_url)
            continue

        frame_count += 1

        # Overlay info
        cv2.putText(frame, f"Frames: {frame_count}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.putText(frame, "Q:Salir S:Foto", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        cv2.imshow("NVR Test Stream", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('s'):
            save_snapshot(frame, frame_count)

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n✅ Finalizado. Frames: {frame_count}")


# =========================
# SNAPSHOT
# =========================
def save_snapshot(frame, frame_count):
    snapshot_dir = current_dir / "snapshots"
    snapshot_dir.mkdir(exist_ok=True)

    filename = f"snapshot_{frame_count}.jpg"
    filepath = snapshot_dir / filename

    cv2.imwrite(str(filepath), frame)
    print(f"💾 Guardado: {filepath}")


# =========================
# MAIN
# =========================
def main():
    print("=" * 60)
    print("🚀 TEST COMPLETO NVR PIPELINE")
    print("=" * 60)

    # 1. Discovery
    cameras = discover_cameras()

    # 2. Selección o manual
    if cameras:
        camera = select_camera(cameras)
        rtsp_url = camera.get("rtsp_url")
    else:
        rtsp_url = input("Ingresa URL RTSP manual: ")

    if not rtsp_url:
        print("❌ URL requerida")
        return

    # 3. Stream
    run_stream(rtsp_url)


if __name__ == "__main__":
    main()