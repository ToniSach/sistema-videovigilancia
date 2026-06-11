"""
================================================================================
MÓDULO: ui.error_guard — Red de seguridad global ante excepciones no controladas
================================================================================

PROPÓSITO
    Hacer la app de escritorio "irrompible" frente a errores imprevistos: en vez
    de que una excepción no capturada (en un slot de Qt, un callback o un hilo)
    cierre la aplicación de golpe, se REGISTRA en el log y se muestra un aviso
    discreto al usuario, manteniendo la app viva.

QUÉ INSTALA
    1. sys.excepthook .......... captura excepciones del hilo principal.
    2. threading.excepthook .... captura excepciones de hilos (QThread/worker).
    3. QApplication.notify ..... envoltura que captura excepciones lanzadas
                                 dentro del despacho de eventos/slots de Qt
                                 (es donde más se rompen las apps PySide).

USO
    Llamar `install_error_guard(app)` justo después de crear el QApplication,
    antes de mostrar la ventana. Es idempotente.

NOTA
    KeyboardInterrupt y SystemExit se dejan pasar (cierre intencionado). Los
    fallos al mostrar el diálogo se tragan: la red de seguridad NUNCA debe ser
    la causa de un cierre.
================================================================================
"""
import logging
import sys
import threading

logger = logging.getLogger(__name__)

_installed = False


def _format_exc(exc_type, exc_value) -> str:
    name = getattr(exc_type, "__name__", str(exc_type))
    return f"{name}: {exc_value}"


def _show_dialog(message: str) -> None:
    """Muestra un aviso no fatal. Se invoca solo desde el hilo de UI."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Se produjo un error")
        box.setText(
            "Ha ocurrido un error inesperado, pero la aplicación sigue "
            "funcionando.\n\nSi el problema se repite, reinicia la aplicación."
        )
        box.setDetailedText(message)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()
    except Exception:
        # Jamás dejar que el propio aviso provoque un cierre.
        pass


def install_error_guard(app=None) -> None:
    """
    Instala las redes de seguridad globales. Idempotente.

    Inputs: app — el QApplication (opcional; se usa para envolver notify()).
    Efecto: a partir de aquí, las excepciones no controladas se loguean y se
        avisan en vez de cerrar la app.
    """
    global _installed
    if _installed:
        return
    _installed = True

    # 1) Hilo principal -----------------------------------------------------
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error(
            "Excepción no controlada (hilo principal)",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        _show_dialog(_format_exc(exc_type, exc_value))

    sys.excepthook = _excepthook

    # 2) Hilos secundarios --------------------------------------------------
    # (QThreads de descarga, workers del api_client, etc.). No mostramos
    # diálogo desde un hilo no-UI (Qt no lo permite con seguridad); solo log.
    def _thread_excepthook(args):
        if issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
            return
        logger.error(
            f"Excepción no controlada en hilo '{args.thread.name}'",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    try:
        threading.excepthook = _thread_excepthook
    except Exception:
        pass

    # 3) Despacho de eventos/slots de Qt ------------------------------------
    if app is not None:
        try:
            _orig_notify = app.notify

            def _safe_notify(receiver, event):
                try:
                    return _orig_notify(receiver, event)
                except Exception as e:
                    logger.error(
                        f"Excepción no controlada en un slot/evento de Qt: {e}",
                        exc_info=True,
                    )
                    _show_dialog(_format_exc(type(e), e))
                    return False

            app.notify = _safe_notify
        except Exception:
            # Algunas plataformas no permiten reasignar notify; el excepthook
            # del hilo principal sigue protegiendo.
            logger.debug("No se pudo envolver QApplication.notify", exc_info=True)

    logger.info("Red de seguridad de excepciones instalada")
