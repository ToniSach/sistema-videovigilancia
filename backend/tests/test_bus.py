"""
Test de descubrimiento ONVIF - Busca cámaras en la red local
Guarda resultados en discovered_cameras.json para usar en otros tests
"""

import sys
import os
from pathlib import Path
import json
import logging

# Configurar path
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent.absolute()
project_dir = backend_dir.parent.absolute()
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

logging.basicConfig(level=logging.INFO, format='%(message)s')

from backend.app.cameras.onvif_discovery import ONVIFDiscovery

def main():
    print("=" * 60)
    print("🔍 TEST: DESCUBRIMIENTO DE CÁMARAS ONVIF")
    print("=" * 60)
    print("Buscando dispositivos en la red local...")
    print("(Esto puede tardar 5-10 segundos)\n")
    
    discovery = ONVIFDiscovery()
    
    try:
        # Timeout de 5 segundos para respuestas
        cameras = discovery.discover(timeout=5)
        
        if not cameras:
            print("❌ No se encontraron cámaras ONVIF en la red")
            print("   Asegúrate de que:")
            print("   • Las cámaras están en la misma red (LAN)")
            print("   • Tienen ONVIF habilitado en su configuración")
            print("   • No están bloqueadas por firewall")
            return
        
        print(f"✅ Se encontraron {len(cameras)} cámara(s):\n")
        
        for i, cam in enumerate(cameras, 1):
            print(f"📷 CÁMARA {i}")
            print(f"   Nombre: {cam.get('name', 'Desconocida')}")
            print(f"   IP: {cam.get('ip', 'N/A')}")
            print(f"   Modelo: {cam.get('manufacturer', 'N/A')} {cam.get('model', '')}")
            print(f"   RTSP URL: {cam.get('rtsp_url', 'No disponible')}")
            print(f"   ONVIF URL: {cam.get('onvif_url', 'N/A')}")
            print(f"   Capacidades:")
            print(f"      - PTZ: {'✅' if cam.get('has_ptz') else '❌'}")
            print(f"      - LEDs/IR: {'✅' if cam.get('has_leds') else '❌'}")
            print(f"      - Audio: {'✅' if cam.get('has_audio') else '❌'}")
            
            if cam.get('profiles'):
                print(f"   Perfiles disponibles: {len(cam['profiles'])}")
                for profile in cam['profiles'][:2]:  # Mostrar máx 2
                    print(f"      • {profile.get('name', 'N/A')}: "
                          f"{profile.get('width', 0)}x{profile.get('height', 0)} @ "
                          f"{profile.get('fps', 0)}fps")
            print()
        
        # Guardar resultados para otros tests
        output_file = current_dir / "discovered_cameras.json"
        with open(output_file, "w", encoding='utf-8') as f:
            json.dump(cameras, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Resultados guardados en: {output_file}")
        print("   Puedes usar este archivo para configurar automáticamente")
        print("   los tests de streaming y control.\n")
        
    except Exception as e:
        print(f"❌ Error en descubrimiento: {e}")
        print("   Verifica que tengas instalado: pip install onvif-zeep wsdiscovery")

if __name__ == "__main__":
    main()