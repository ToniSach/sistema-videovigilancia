# C:\Users\tonis\OneDrive\Documentos\ProyectoPrueba-TT\Proyecto-kimi\sistema-videovigilancia\backend\tests\test_onvif_discovery.py
"""
Script de diagnóstico ONVIF para detectar problemas de descubrimiento.
Ejecutar: python test_onvif_discovery.py
"""

import sys
import os
import socket
import subprocess
import logging
import time
from datetime import datetime

# Configurar logging inmediato
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('onvif_debug.log', mode='w')
    ]
)
logger = logging.getLogger(__name__)

# Agregar el path del proyecto para imports
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(current_dir, '..', 'app')
sys.path.insert(0, os.path.abspath(backend_dir))

def check_prerequisites():
    """Verifica prerequisitos del sistema."""
    print("\n" + "="*60)
    print("🔍 DIAGNÓSTICO DE CÁMARA ONVIF")
    print("="*60)
    print(f"Fecha/Hora: {datetime.now()}")
    print(f"Python: {sys.version}")
    print(f"Directorio: {os.getcwd()}")
    print("-"*60)
    
    # 1. Verificar si estamos en Windows y el firewall
    if sys.platform == "win32":
        print("\n⚠️  ADVERTENCIA WINDOWS:")
        print("   El Firewall de Windows suele bloquear WS-Discovery (puerto 3702 UDP)")
        print("   Si no detecta cámaras, prueba:")
        print("   1. Desactivar temporalmente el firewall")
        print("   2. O agregar regla para Python en el firewall")
        print("   3. Ejecutar como Administrador")
    
    # 2. Verificar red
    print("\n📡 INFORMACIÓN DE RED:")
    try:
        hostname = socket.gethostname()
        ip_local = socket.gethostbyname(hostname)
        print(f"   Hostname: {hostname}")
        print(f"   IP Local: {ip_local}")
        
        # Obtener todas las interfaces
        print("\n   Interfaces de red:")
        try:
            import netifaces
            for interface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(interface)
                if netifaces.AF_INET in addrs:
                    for addr in addrs[netifaces.AF_INET]:
                        print(f"   • {interface}: {addr.get('addr')} / {addr.get('netmask')}")
        except ImportError:
            print("   (Instala 'pip install netifaces' para ver todas las interfaces)")
    except Exception as e:
        logger.error(f"Error obteniendo info de red: {e}")

def test_wsdiscovery_module():
    """Prueba el módulo wsdiscovery directamente."""
    print("\n" + "="*60)
    print("🔍 PRUEBA 1: WS-Discovery (Multicast)")
    print("="*60)
    
    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
        
        print("Creando servicio WS-Discovery...")
        wsd = WSDiscovery()
        
        print("Iniciando búsqueda multicast (timeout 10 segundos)...")
        wsd.start()
        
        # Buscar servicios de tipo ONVIF
        from wsdiscovery import QName
        # ONVIF Device service type
        onvif_type = QName("http://www.onvif.org/ver10/network/wsdl", "NetworkVideoTransmitter")
        
        services = wsd.searchServices(types=[onvif_type], timeout=10)
        
        print(f"\n   ✅ Búsqueda completada. Servicios encontrados: {len(services)}")
        
        if not services:
            print("\n   ❌ No se encontraron cámaras vía WS-Discovery")
            print("   Posibles causas:")
            print("   • Firewall bloqueando puerto 3702 UDP (multicast)")
            print("   • Cámara no soporta WS-Discovery")
            print("   • Cámara en red diferente/subred")
            print("   • Cámara apagada o desconectada")
        else:
            for i, service in enumerate(services, 1):
                print(f"\n   📷 Dispositivo {i}:")
                print(f"      XAddrs: {service.getXAddrs()}")
                print(f"      Types: {service.getTypes()}")
                print(f"      EPRef: {service.getEPR()}")
                
                # Intentar extraer IP
                try:
                    xaddr = service.getXAddrs()[0]
                    ip = xaddr.split("//")[1].split("/")[0].split(":")[0]
                    print(f"      IP detectada: {ip}")
                except:
                    pass
        
        wsd.stop()
        return len(services) > 0
        
    except Exception as e:
        logger.error(f"Error en WS-Discovery: {e}", exc_info=True)
        print(f"\n   ❌ Error: {e}")
        print("   Asegúrate de tener instalado: pip install wsdiscovery")
        return False

def test_onvif_direct(ip=None):
    """Prueba conexión ONVIF directa a una IP específica."""
    print("\n" + "="*60)
    print("🔍 PRUEBA 2: Conexión ONVIF Directa")
    print("="*60)
    
    # Si no se proporciona IP, pedirla
    if not ip:
        ip = input("\n   Ingresa IP de la cámara para probar (ej: 192.168.1.100): ").strip()
        if not ip:
            print("   Saltando prueba directa...")
            return
    
    ports = [80, 8080, 8000, 8899, 2020]
    credentials = [
        ("admin", "admin"),
        ("admin", "12345"),
        ("admin", "123456"),
        ("admin", ""),
        ("root", "root"),
        ("user", "user")
    ]
    
    print(f"\n   Probando cámara en {ip}...")
    print(f"   Puertos a probar: {ports}")
    
    # Primero verificar si responde a ping
    print(f"\n   📡 Verificando conectividad básica...")
    try:
        # Ping en Windows
        result = subprocess.run(['ping', '-n', '1', '-w', '1000', ip], 
                            capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   ✅ Ping exitoso: {ip} responde")
        else:
            print(f"   ⚠️  Ping falló: {ip} no responde ICMP (puede estar bloqueado)")
    except Exception as e:
        print(f"   ⚠️  No se pudo ejecutar ping: {e}")
    
    # Probar puertos TCP
    print(f"\n   🔌 Escaneando puertos ONVIF...")
    puertos_abiertos = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((ip, port))
        if result == 0:
            print(f"   ✅ Puerto {port}: ABIERTO")
            puertos_abiertos.append(port)
        else:
            print(f"   ❌ Puerto {port}: Cerrado/Filtrado")
        sock.close()
    
    if not puertos_abiertos:
        print("\n   ❌ No se encontraron puertos ONVIF abiertos")
        print("   La cámara puede estar:")
        print("   • Apagada")
        print("   • En diferente red")
        print("   • Con firewall habilitado")
        print("   • Usando puertos no estándar")
        return
    
    # Intentar ONVIF en puertos abiertos
    print(f"\n   📷 Probando protocolo ONVIF...")
    
    try:
        from onvif import ONVIFCamera
        
        for port in puertos_abiertos:
            print(f"\n   Probando puerto {port}...")
            
            for user, pwd in credentials:
                try:
                    print(f"      Credencial: {user}/{pwd}...", end=" ")
                    cam = ONVIFCamera(ip, port, user, pwd, timeout=5)
                    
                    # Intentar obtener info del dispositivo
                    dev_mgmt = cam.create_devicemgmt_service()
                    device_info = dev_mgmt.GetDeviceInformation()
                    
                    print(f"\n      ✅ ¡CONECTADO!")
                    print(f"         Fabricante: {device_info.Manufacturer}")
                    print(f"         Modelo: {device_info.Model}")
                    print(f"         Firmware: {device_info.FirmwareVersion}")
                    print(f"         Serial: {device_info.SerialNumber}")
                    
                    # Probar media service
                    try:
                        media = cam.create_media_service()
                        profiles = media.GetProfiles()
                        print(f"         Perfiles encontrados: {len(profiles)}")
                        
                        if profiles:
                            # Obtener stream URI
                            profile = profiles[0]
                            stream_uri = media.GetStreamUri({
                                'StreamSetup': {
                                    'Stream': 'RTP-Unicast',
                                    'Transport': {'Protocol': 'RTSP'}
                                },
                                'ProfileToken': profile.token
                            })
                            print(f"         RTSP URL: {stream_uri.Uri}")
                    except Exception as e:
                        print(f"         ⚠️  Error media: {e}")
                    
                    # Guardar resultado exitoso
                    with open('camara_detectada.txt', 'w') as f:
                        f.write(f"IP: {ip}\n")
                        f.write(f"Puerto: {port}\n")
                        f.write(f"Usuario: {user}\n")
                        f.write(f"Contraseña: {pwd}\n")
                        f.write(f"Fabricante: {device_info.Manufacturer}\n")
                        f.write(f"Modelo: {device_info.Model}\n")
                    
                    return  # Éxito, salir
                    
                except Exception as e:
                    print(f"Falló ({str(e)[:50]})")
                    continue
    
    except ImportError:
        print("   ❌ Módulo onvif-zeep no instalado")
        print("   Ejecuta: pip install onvif-zeep")
    except Exception as e:
        logger.error(f"Error en prueba ONVIF: {e}")

def test_discovery_class():
    """Prueba la clase ONVIFDiscovery del proyecto."""
    print("\n" + "="*60)
    print("🔍 PRUEBA 3: Clase ONVIFDiscovery del Proyecto")
    print("="*60)
    
    try:
        from cameras.onvif_discovery import ONVIFDiscovery
        
        print("   Inicializando ONVIFDiscovery...")
        discovery = ONVIFDiscovery()
        
        print("   Ejecutando descubrimiento (timeout 10s)...")
        devices = discovery.discover(timeout=10)
        
        print(f"\n   ✅ Descubrimiento completado")
        print(f"   Cámaras encontradas: {len(devices)}")
        
        for i, dev in enumerate(devices, 1):
            print(f"\n   📷 Cámara {i}:")
            for key, value in dev.items():
                print(f"      {key}: {value}")
                
    except Exception as e:
        logger.error(f"Error usando ONVIFDiscovery: {e}", exc_info=True)
        print(f"\n   ❌ Error: {e}")

def main():
    """Función principal de diagnóstico."""
    
    check_prerequisites()
    
    # Prueba 1: WS-Discovery
    found_wsd = test_wsdiscovery_module()
    
    # Prueba 2: Si no encontró nada, probar IP directa
    if not found_wsd:
        respuesta = input("\n¿Deseas probar una IP específica? (s/n): ").lower()
        if respuesta == 's':
            test_onvif_direct()
    
    # Prueba 3: Clase del proyecto
    print("\n")
    resp = input("¿Probar la clase ONVIFDiscovery del proyecto? (s/n): ").lower()
    if resp == 's':
        test_discovery_class()
    
    # Resumen final
    print("\n" + "="*60)
    print("📋 RESUMEN Y RECOMENDACIONES")
    print("="*60)
    
    print("""
Si no se detectaron cámaras:

1. FIREWALL DE WINDOWS (Causa #1):
   - Abre "Firewall de Windows Defender"
   - Click en "Permitir una aplicación..."
   - Busca Python y marca las casillas de Red Privada/Pública
   - O desactiva temporalmente el firewall para probar

2. RED LOCAL:
   - Asegúrate que la PC y la cámara están en la misma red
   - Prueba hacer ping a la cámara: ping [IP_DE_CAMARA]

3. CÁMARA ESPECÍFICA:
   - Algunas cámaras chinas usan protocolos propietarios
   - Prueba agregar la cámara manualmente con la IP
   - Verifica que tenga ONVIF habilitado en su configuración web

4. PUERTOS ALTERNATIVOS:
   - Algunas cámaras usan: 8080, 8000, 8899
   - Revisa el manual de tu cámara

5. LOGS:
   - Revisa el archivo 'onvif_debug.log' generado
   - Compártelo si necesitas soporte
    """)
    
    input("\nPresiona ENTER para salir...")

if __name__ == "__main__":
    main()