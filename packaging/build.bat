@echo off
REM ============================================================================
REM  Compila el producto NVR-VMS en un .exe de Windows (carpeta onedir).
REM  Resultado final:  %BUILD_ROOT%\dist\NVR-VMS\NVR-VMS.exe
REM
REM  Requisitos: ejecutar desde el repo con el entorno virtual `env` presente
REM  (trae torch, PySide6, ultralytics, etc.). PostgreSQL, VLC, ffmpeg y
REM  go2rtc deben existir en la máquina (stage_vendor.py los localiza).
REM ============================================================================
setlocal

REM --- Rutas -----------------------------------------------------------------
set "REPO_DIR=%~dp0.."
pushd "%REPO_DIR%"
set "REPO_DIR=%CD%"
popd

if "%BUILD_ROOT%"=="" set "BUILD_ROOT=C:\NVR-VMS-build"
set "VENDOR_DIR=%BUILD_ROOT%\vendor"
set "PY=%REPO_DIR%\env\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
for /f "delims=" %%i in ('"%PY%" -c "import sysconfig;print(sysconfig.get_paths()['purelib'])"') do set "SITE_PACKAGES=%%i"

echo ============================================================
echo  REPO_DIR      = %REPO_DIR%
echo  BUILD_ROOT    = %BUILD_ROOT%
echo  VENDOR_DIR    = %VENDOR_DIR%
echo  SITE_PACKAGES = %SITE_PACKAGES%
echo  PYTHON        = %PY%
echo ============================================================

REM --- 1) PyInstaller ---------------------------------------------------------
echo [1/5] Comprobando PyInstaller...
"%PY%" -c "import PyInstaller" 2>NUL || "%PY%" -m pip install pyinstaller || goto :fail

REM --- 2) Staging de binarios -------------------------------------------------
echo [2/5] Reuniendo binarios (ffmpeg, go2rtc, PostgreSQL, VLC, modelo)...
"%PY%" "%REPO_DIR%\packaging\stage_vendor.py" || goto :fail

REM --- 3) Backend -------------------------------------------------------------
echo [3/5] Compilando NVR-Backend.exe (esto tarda varios minutos)...
"%PY%" -m PyInstaller --noconfirm --clean ^
  --distpath "%BUILD_ROOT%\dist" --workpath "%BUILD_ROOT%\work" ^
  "%REPO_DIR%\packaging\nvr_backend.spec" || goto :fail

REM --- 4) Desktop / launcher --------------------------------------------------
echo [4/5] Compilando NVR-VMS.exe (GUI + launcher)...
"%PY%" -m PyInstaller --noconfirm --clean ^
  --distpath "%BUILD_ROOT%\dist" --workpath "%BUILD_ROOT%\work" ^
  "%REPO_DIR%\packaging\nvr_desktop.spec" || goto :fail

REM --- 5) Ensamblar el backend dentro de la carpeta del producto -------------
echo [5/5] Ensamblando producto final...
robocopy "%BUILD_ROOT%\dist\NVR-Backend" "%BUILD_ROOT%\dist\NVR-VMS\backend" /E /NFL /NDL /NJH /NJS /NC /NS >NUL
if errorlevel 8 goto :fail

echo.
echo ============================================================
echo  LISTO. Producto en:
echo    %BUILD_ROOT%\dist\NVR-VMS
echo  Ejecutable principal:
echo    %BUILD_ROOT%\dist\NVR-VMS\NVR-VMS.exe
echo ============================================================
endlocal
exit /b 0

:fail
echo.
echo *** ERROR en la compilacion. Revisa el log de arriba. ***
endlocal
exit /b 1
