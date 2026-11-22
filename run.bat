@echo off
setlocal EnableExtensions
rem ===========================================================================
rem Chatbot de pre-revision de seguridad - Banco Andesur (FastAPI, Python 3.12)
rem Basado en el producto de la sesion 7 del curso DMC.
rem
rem Uso:
rem    run.bat              configura el entorno y sale
rem    run.bat --descargar  vuelve a bajar las fuentes oficiales (opcional)
rem    run.bat --importar --desde "C:\mis_fuentes"   suma fuentes de una carpeta local
rem    run.bat --indexar    construye la base vectorial Chroma (una sola vez)
rem    run.bat --iniciar    arranca el servidor en http://127.0.0.1:8040
rem    run.bat --tests      corre las pruebas (sin red y sin clave)
rem    run.bat --evaluar    corre los casos de prueba y mide los hallazgos
rem    run.bat --logs       abre el log del turno (data\logs\eventos.jsonl)
rem    run.bat --check      diagnostico rapido, sin instalar nada
rem    run.bat --reparar    borra el .venv y lo recrea desde cero
rem    run.bat --diag       informe completo en diagnostico.txt (para reportar problemas)
rem    run.bat --offline    instala desde la carpeta wheels\ (sin internet)
rem
rem Todo lo que pasa queda en run.log. Si algo falla, ese archivo lo dice.
rem ===========================================================================
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: no se pudo entrar a la carpeta del proyecto.
    echo Mueve la carpeta a una ruta simple, por ejemplo C:\proyectos\andesur
    pause
    exit /b 1
)

set "SESION=Andesur"
set "PUERTO=8040"
set "LOG=%~dp0run.log"
set "DIAG=%~dp0diagnostico.txt"
set "PY_VERSION=3.12"
set "PY_MAJOR=3"
set "PY_MINOR=12"
set "IMPORTS=fastapi, uvicorn, pydantic, dotenv, sklearn, pypdf, spacy, sentence_transformers, torch, numpy, httpx, pytest, chromadb, bs4"

title Chatbot %SESION% - Python %PY_VERSION%
rem sentence-transformers intenta importar TensorFlow si lo encuentra instalado
rem y con Keras 3 falla: "ValueError: ... install the tf-keras package".
rem Estas dos variables apagan esa ruta; sin ellas la carga del modelo revienta.
set "USE_TF=0"
set "TRANSFORMERS_NO_TF=1"

rem Aisla el script de configuraciones globales que suelen romper los entornos:
rem PYTHONHOME/PYTHONPATH de Anaconda, paquetes del usuario y kernel global.
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "PYTHONNOUSERSITE=1"
set "PIP_USER=0"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
rem UTF-8 en Python: evita errores con tildes, enes y rutas con acentos.
set "PYTHONUTF8=1"

rem --- Modo de ejecucion -----------------------------------------------------
set "SOLO_VERIFICAR="
set "SOLO_INSTALAR="
set "FORZAR_REPARAR="
set "MODO_DIAG="
set "MODO_OFFLINE="
set "MODO_INICIAR="
set "MODO_FLAG="
if /i "%~1"=="--check"    set "SOLO_VERIFICAR=1"
if /i "%~1"=="--instalar" set "SOLO_INSTALAR=1"
if /i "%~1"=="--reparar"  set "FORZAR_REPARAR=1"
if /i "%~1"=="--diag"     set "MODO_DIAG=1"
if /i "%~1"=="--offline"  set "MODO_OFFLINE=1"
if /i "%~1"=="--iniciar"  set "MODO_INICIAR=1"
if /i "%~1"=="--logs"     set "MODO_LOGS=1"
if /i "%~1"=="--descargar" set "MODO_SCRIPT=scripts.descargar_fuentes"
if /i "%~1"=="--importar" set "MODO_SCRIPT=scripts.importar_fuentes"
if /i "%~1"=="--indexar"  set "MODO_SCRIPT=scripts.indexar"
if /i "%~1"=="--evaluar"  set "MODO_SCRIPT=scripts.evaluar_casos"
if /i "%~1"=="--tests"    set "MODO_TESTS=1"
if defined SOLO_VERIFICAR set "MODO_FLAG=1"
if defined SOLO_INSTALAR  set "MODO_FLAG=1"
if defined MODO_DIAG      set "MODO_FLAG=1"

call :log "===== inicio: sesion %SESION% %DATE% %TIME% ====="
call :log "carpeta: %~dp0"

rem --- Entorno del equipo ----------------------------------------------------
call :revisar_ruta_larga
call :comprobar_red
call :espacio_libre
if defined MODO_DIAG goto :informe

if defined FORZAR_REPARAR (
    echo.
    echo Modo reparar: se eliminara el entorno virtual actual.
    call :apartar_venv
)

rem --- Buscar Python 3.12 ----------------------------------------------------
set "PY_EXE="
call :buscar_python
if not defined PY_EXE goto :sin_python

"%PY_EXE%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(%PY_MAJOR%,%PY_MINOR%) else 1)" >nul 2>nul
if errorlevel 1 goto :version_incorrecta
call :log "python: %PY_EXE%"

"%PY_EXE%" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" >nul 2>nul
if errorlevel 1 goto :python_32

if defined SOLO_VERIFICAR (
    echo Python %PY_VERSION% encontrado:
    "%PY_EXE%" --version
    echo   "%PY_EXE%"
    call :revisar_venv_check
    call :diagnostico_venv
    echo.
    if defined HAY_RED (echo Red: OK hacia pypi.org) else (echo Red: no se pudo comprobar pypi.org)
    if defined LIBRE_MB echo Espacio libre: %LIBRE_MB% MB
    if defined RUTA_LARGA echo AVISO: ruta del proyecto muy larga, puede fallar en Windows.
    echo.
    echo Log de esta ejecucion: %LOG%
    exit /b 0
)

rem --- Preparar entorno y dependencias ---------------------------------------
call :revisar_venv
if errorlevel 1 goto :pausa_error

if not exist ".venv\Scripts\python.exe" (
    rem Carpeta .venv a medias (sin python.exe): se aparta antes de crear.
    if exist ".venv" call :apartar_venv
    echo Creando entorno virtual con Python %PY_VERSION%...
    "%PY_EXE%" -m venv .venv
    if errorlevel 1 goto :error_venv
    if not exist ".venv\Scripts\python.exe" goto :error_venv
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
)

rem Un .venv puede existir y estar roto (copiado de otro equipo, pip borrado,
rem o con una base de Python desinstalada). Se comprueba y se aparta.
call :venv_sano
if errorlevel 1 (
    echo.
    echo AVISO: el entorno virtual existente no funciona. Se recrea.
    call :apartar_venv
    "%PY_EXE%" -m venv .venv
    if errorlevel 1 goto :error_venv
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
    call :venv_sano
    if errorlevel 1 goto :error_venv
)

set "PATH=%~dp0.venv\Scripts;%PATH%"

".venv\Scripts\python.exe" -c "import %IMPORTS%" >nul 2>nul
if errorlevel 1 (
    call :instalar_dependencias
    if errorlevel 1 goto :error_deps
    ".venv\Scripts\python.exe" -c "import %IMPORTS%" >nul 2>nul
    if errorlevel 1 goto :error_deps
)

if not exist ".env" if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo Se creo .env a partir de .env.example.
    call :log ".env creado desde .env.example"
)


if defined SOLO_INSTALAR (
    echo.
    echo Entorno listo. Para arrancarlo:  run.bat --iniciar
    call :log "instalacion completada"
    exit /b 0
)

rem --- Ver el log (solo con --logs) -------------------------------------------
if defined MODO_LOGS (
    if not exist "data\logs\eventos.jsonl" (
        echo Todavia no hay log: arranca el servicio con  run.bat --iniciar
        echo y conversa un turno; despues vuelve a ejecutar  run.bat --logs
        exit /b 0
    )
    echo Abriendo data\logs\eventos.jsonl
    start "" notepad "data\logs\eventos.jsonl"
    exit /b 0
)

rem --- Scripts y pruebas (--descargar, --importar, --indexar, --evaluar, --tests)
if defined MODO_SCRIPT (
    call :log "script: %MODO_SCRIPT%"
    ".venv\Scripts\python.exe" -m %MODO_SCRIPT% %2 %3 %4 %5
    exit /b %errorlevel%
)
if defined MODO_TESTS (
    ".venv\Scripts\python.exe" -m pytest -q
    exit /b %errorlevel%
)
if not exist "chroma_db" if defined MODO_INICIAR (
    echo.
    echo AVISO: todavia no existe la base vectorial ^(chroma_db^).
    echo El chat arranca, pero sin lineamientos. Para crearla:
    echo    run.bat --indexar
    echo.
)

rem --- Arrancar el servidor (solo con --iniciar) ------------------------------
if not defined MODO_INICIAR (
    echo.
    echo Entorno listo. Para arrancar el servicio:
    echo.
    echo    run.bat --iniciar
    echo.
    echo Y abrir en el navegador:  http://127.0.0.1:8040   ^(docs: /docs^)
    echo Si prefieres tu propia terminal:
    echo    .venv\Scripts\python.exe -m uvicorn app.main:app --port 8040
    echo.
    exit /b 0
)

echo.
echo Arrancando  [Python %PY_VERSION%]
echo Ctrl+C para detener.
echo.
start "" http://127.0.0.1:8040
".venv\Scripts\python.exe" -m uvicorn app.main:app --port %PUERTO%
set "CODIGO=%errorlevel%"
if not "%CODIGO%"=="0" (
    echo.
    echo El servidor termino con codigo %CODIGO%. Revisa el mensaje de arriba.
    echo Diagnostico completo:  run.bat --diag
    pause
)
call :log "fin: codigo %CODIGO%"
exit /b %CODIGO%


rem ===========================================================================
rem  Informe de diagnostico (--diag): se imprime y queda en diagnostico.txt
rem ===========================================================================
:informe
call :diag > "%DIAG%" 2>&1
type "%DIAG%"
echo.
echo Informe guardado en: %DIAG%
echo Adjunta ese archivo si reportas un problema.
exit /b 0

:diag
echo ============================================================
echo  DIAGNOSTICO - Chatbot %SESION%
echo ============================================================
echo Fecha:        %DATE% %TIME%
echo Carpeta:      %~dp0
echo Windows:      %OS%
ver
echo Arquitectura: %PROCESSOR_ARCHITECTURE%
for /f "tokens=*" %%C in ('chcp') do echo Codepage:    %%C
echo.
echo --- Log de ejecuciones anteriores (ultimas 20 lineas) ---
if exist "%LOG%" (
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 20" 2>nul
) else (
    echo [sin log todavia]
)
echo.
echo --- Red ---
if defined HAY_RED (echo pypi.org: accesible) else (echo pypi.org: NO accesible ^(sin internet, proxy o firewall^))
echo HTTP_PROXY=%HTTP_PROXY%
echo HTTPS_PROXY=%HTTPS_PROXY%
echo PIP_INDEX_URL=%PIP_INDEX_URL%
echo.
echo --- Espacio en disco ---
powershell -NoProfile -Command "$d=Get-Item -LiteralPath '%~dp0'; Get-PSDrive -PSProvider FileSystem -Name ($d.PSDrive.Name) | ForEach-Object { 'unidad ' + $_.Name + ': libre ' + [int]($_.Free/1MB) + ' MB de ' + [int](($_.Used+$_.Free)/1MB) + ' MB' }" 2>nul
echo.
echo --- Pythons encontrados ---
for /f "delims=" %%I in ('py -0p 2^>nul') do echo   %%I
for /f "delims=" %%I in ('uv python list --only-installed 2^>nul') do echo   uv: %%I
echo   PATH python:
for /f "delims=" %%I in ('python -c "import sys; print(sys.executable, sys.version.split()[0])" 2^>nul') do echo     %%I
echo.
echo --- Entorno virtual del proyecto ---
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; print('  .venv:', sys.version.split()[0], sys.executable)"
    ".venv\Scripts\python.exe" -c "import %IMPORTS%; print('  dependencias: OK')" 2>nul || echo   dependencias: FALTAN o ROTAS
    ".venv\Scripts\python.exe" -m pip --version 2>nul || echo   pip: no responde
) else (
    echo   .venv: no existe
)
echo.
echo --- Paquetes instalados en .venv ---
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip list 2>nul
echo.
echo --- Configuracion de pip ---
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -m pip config list 2>nul
echo.
echo ============================================================
echo  Fin del informe
echo ============================================================
exit /b 0


rem ===========================================================================
rem  Utilidades
rem ===========================================================================
:log
echo [%DATE% %TIME%] %~1 >>"%LOG%"
exit /b 0

:revisar_ruta_larga
set "RUTA_LARGA="
rem Mas de ~120 caracteres: los archivos internos del .venv rozan el limite de
rem 260 de Windows y la instalacion empieza a fallar de forma intermitente.
set "TMP_RUTA=%~dp0"
set "CONTADOR=0"
call :contar_ruta
if %CONTADOR% GTR 120 set "RUTA_LARGA=1"
if defined RUTA_LARGA (
    echo.
    echo AVISO: la ruta del proyecto es muy larga ^(%CONTADOR% caracteres^).
    echo Windows puede fallar al instalar. Si aparece un error raro, mueve
    echo esta carpeta a algo corto, por ejemplo C:\proyectos\andesur
    call :log "AVISO: ruta larga (%CONTADOR%)"
)
exit /b 0

:contar_ruta
set "CONTADOR=0"
set "RESTO=%TMP_RUTA%"
:contar_bucle
if not defined RESTO exit /b 0
set "RESTO=%RESTO:~1%"
set /a CONTADOR+=1
goto :contar_bucle

:comprobar_red
set "HAY_RED="
if exist "%SystemRoot%\System32\curl.exe" (
    "%SystemRoot%\System32\curl.exe" -s -I --max-time 8 https://pypi.org/simple/ >nul 2>nul
    if not errorlevel 1 set "HAY_RED=1"
)
if not defined HAY_RED call :log "sin red hacia pypi.org"
exit /b 0

:espacio_libre
set "LIBRE_MB="
for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=Get-Item -LiteralPath '%~dp0'; [int]((Get-PSDrive -PSProvider FileSystem -Name ($d.PSDrive.Name)).Free/1MB)" 2^>nul`) do set "LIBRE_MB=%%L"
if not defined LIBRE_MB exit /b 0
if %LIBRE_MB% LSS 2500 (
    echo.
    echo AVISO: solo quedan %LIBRE_MB% MB libres en el disco.
    echo Este proyecto necesita unos 3 GB. Libera espacio antes de continuar.
    call :log "AVISO: espacio libre bajo: %LIBRE_MB% MB"
)
exit /b 0


rem ===========================================================================
rem  Busqueda de Python 3.12 de 64 bits, con instalacion automatica si falta
rem ===========================================================================
:buscar_python
rem 1. Launcher py
for /f "delims=" %%I in ('py -%PY_VERSION% -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%I"
call :validar_python
if defined PY_EXE exit /b 0

rem 2. uv (instala y localiza sin permisos de administrador)
where uv >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%I in ('uv python find --system %PY_VERSION% 2^>nul') do set "PY_EXE=%%I"
    call :validar_python
    if defined PY_EXE exit /b 0
    if not defined MODO_FLAG (
        echo No se encontro Python %PY_VERSION%. Instalando con uv...
        call :log "instalando python %PY_VERSION% con uv"
        uv python install %PY_VERSION%
        for /f "delims=" %%I in ('uv python find --system %PY_VERSION% 2^>nul') do set "PY_EXE=%%I"
        call :validar_python
        if defined PY_EXE exit /b 0
    )
)

rem 3. python / python3 del PATH
for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%I"
call :validar_python
if defined PY_EXE exit /b 0
for /f "delims=" %%I in ('python3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%I"
call :validar_python
if defined PY_EXE exit /b 0

rem 4. Rutas tipicas de instalacion (winget, python.org, Anaconda)
for %%P in (
    "%LocalAppData%\Programs\Python\Python%PY_MAJOR%%PY_MINOR%\python.exe"
    "%ProgramFiles%\Python%PY_MAJOR%%PY_MINOR%\python.exe"
    "C:\Python%PY_MAJOR%%PY_MINOR%\python.exe"
) do (
    set "PY_EXE=%%~P"
    call :validar_python
    if defined PY_EXE exit /b 0
)

rem 5. Instalacion silenciosa, alcance usuario, sin administrador
if defined MODO_FLAG exit /b 0
where winget >nul 2>nul
if not errorlevel 1 (
    echo.
    echo No se encontro Python %PY_VERSION%. Instalando con winget...
    call :log "instalando python %PY_VERSION% con winget"
    winget install -e --id Python.Python.%PY_VERSION% --scope user --silent --accept-package-agreements --accept-source-agreements
    set "PY_EXE=%LocalAppData%\Programs\Python\Python%PY_MAJOR%%PY_MINOR%\python.exe"
    call :validar_python
    if defined PY_EXE exit /b 0
    for /f "delims=" %%I in ('py -%PY_VERSION% -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%I"
    call :validar_python
    if defined PY_EXE exit /b 0
)

rem 6. Ultimo recurso: instalador oficial descargado con curl
if not exist "%SystemRoot%\System32\curl.exe" exit /b 0
echo.
echo No se pudo instalar con uv ni con winget. Se descargara el instalador
echo oficial de Python %PY_VERSION% desde python.org (unos 25 MB).
choice /C SN /M "Continuar? [S/N]"
if errorlevel 2 exit /b 0
call :log "descargando instalador de python.org"
"%SystemRoot%\System32\curl.exe" -L --max-time 300 -o "%TEMP%\python-dmc-setup.exe" https://www.python.org/ftp/python/%PY_VERSION%.9/python-%PY_VERSION%.9-amd64.exe
if errorlevel 1 exit /b 0
echo Instalando en modo silencioso (2-3 minutos)...
"%TEMP%\python-dmc-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
set "PY_EXE=%LocalAppData%\Programs\Python\Python%PY_MAJOR%%PY_MINOR%\python.exe"
call :validar_python
exit /b 0

:validar_python
rem Deja PY_EXE vacia si el valor no es un Python %PY_VERSION% de 64 bits real.
rem Descarta tambien el alias de la Microsoft Store (WindowsApps\python.exe),
rem que abre la tienda en vez de ejecutar Python.
if not defined PY_EXE exit /b 0
if not exist "%PY_EXE%" goto :validar_vacia
rem Sin tuberias ni comandos externos: la sustitucion de variables no falla
rem por permisos ni por codepage.
set "PY_BUSCA=%PY_EXE%"
if not "%PY_BUSCA:WindowsApps=%"=="%PY_BUSCA%" goto :validar_vacia
rem Nunca se usa el python del propio proyecto como interprete base: uv devuelve
rem el .venv del directorio actual si existe y se crearia un venv dentro de otro.
if /i "%PY_BUSCA%"=="%~dp0.venv\Scripts\python.exe" goto :validar_vacia
"%PY_EXE%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(%PY_MAJOR%,%PY_MINOR%) else 1)" >nul 2>nul
if errorlevel 1 goto :validar_vacia
"%PY_EXE%" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" >nul 2>nul
if errorlevel 1 goto :validar_vacia
exit /b 0
:validar_vacia
set "PY_EXE="
exit /b 0


rem ===========================================================================
rem  Entorno virtual: revision, reparacion e instalacion
rem ===========================================================================
:revisar_venv
if not exist ".venv\Scripts\python.exe" exit /b 0
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(%PY_MAJOR%,%PY_MINOR%) else 1)" >nul 2>nul
if not errorlevel 1 exit /b 0
echo.
echo AVISO: .venv existe pero no usa Python %PY_VERSION%. Se recrea.
call :apartar_venv
exit /b 0

:venv_sano
rem devuelve 1 si el .venv no puede ni ejecutar Python o no tiene pip.
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 exit /b 1
)
exit /b 0

:apartar_venv
if not exist ".venv" exit /b 0
if exist ".venv-roto" rmdir /s /q ".venv-roto" >nul 2>nul
move ".venv" ".venv-roto" >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: no se pudo mover .venv. Cierra el servidor, otra consola
    echo o el antivirus y vuelve a intentar.
    call :log "ERROR: no se pudo mover .venv"
    call :pausa_error
    exit /b 1
)
call :log ".venv anterior movido a .venv-roto"
exit /b 0

:revisar_venv_check
if not exist ".venv\Scripts\python.exe" (
    if exist ".venv" (
        echo .venv: incompleto ^(sin python.exe^), se recreara al arrancar
    ) else (
        echo .venv: no existe, se creara al arrancar
    )
    exit /b 0
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(%PY_MAJOR%,%PY_MINOR%) else 1)" >nul 2>nul
if errorlevel 1 (
    echo .venv: version incompatible, se recreara al arrancar
    exit /b 0
)
call :venv_sano
if errorlevel 1 (
    echo .venv: roto ^(Python o pip no responden^), se recreara al arrancar
) else (
    echo .venv: OK
)
exit /b 0

:diagnostico_venv
if not exist ".venv\Scripts\python.exe" exit /b 0
echo Dependencias del .venv:
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sentence_transformers, numpy; print('  fastapi', fastapi.__version__); print('  uvicorn', uvicorn.__version__); print('  sentence-transformers', sentence_transformers.__version__)" 2>nul
if errorlevel 1 echo   faltan dependencias ^(se completaran al arrancar^).
exit /b 0

:instalar_dependencias
if defined MODO_OFFLINE (
    if not exist "%~dp0wheels" (
        echo.
        echo ERROR: modo offline y no existe la carpeta wheels\
        echo Copia ahi los paquetes ^(pip download -r requirements.txt -d wheels^)
        echo   o ejecuta run.bat sin --offline para instalar desde internet.
        call :log "ERROR: --offline sin carpeta wheels"
        exit /b 1
    )
    echo Instalando desde wheels\ ^(sin internet^)...
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links "%~dp0wheels" -r requirements.txt --log "%LOG%"
    exit /b %errorlevel%
)
if not defined HAY_RED (
    echo.
    echo AVISO: no se pudo comprobar internet. Se intentara igual.
    echo Si falla, prueba con:  run.bat --offline   ^(requiere carpeta wheels\^)
)
if defined DMC_PIP_INDEX (
    echo Usando indice de paquetes: %DMC_PIP_INDEX%
    ".venv\Scripts\python.exe" -m pip install --index-url "%DMC_PIP_INDEX%" -r requirements.txt --timeout 60 --retries 3 --log "%LOG%"
) else (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt --timeout 60 --retries 3 --log "%LOG%"
)
if not errorlevel 1 exit /b 0
echo.
echo Primer intento fallido. Reintentando sin cache ^(esto arregla muchos casos^)...
if defined DMC_PIP_INDEX (
    ".venv\Scripts\python.exe" -m pip install --index-url "%DMC_PIP_INDEX%" --no-cache-dir -r requirements.txt --timeout 60 --retries 2 --log "%LOG%"
) else (
    ".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt --timeout 60 --retries 2 --log "%LOG%"
)
if not errorlevel 1 exit /b 0
echo.
echo ERROR: no se pudieron instalar las dependencias.
echo Causas habituales:
echo   - sin internet o con proxy/firewall que bloquea pypi.org
echo   - antivirus bloqueando la instalacion
echo   - disco lleno
echo Detalle tecnico en: %LOG%
echo Si el equipo no tiene internet, prepara la carpeta wheels\ en otro equipo y ejecuta:
echo   run.bat --offline
call :log "ERROR: fallo pip tras dos intentos"
exit /b 1


rem ===========================================================================
rem  Salidas de error
rem ===========================================================================
:sin_python
echo.
echo ERROR: no se encontro Python %PY_VERSION% de 64 bits en este equipo.
echo.
echo Opciones:
echo   1. Ejecutar run.bat de nuevo: intenta instalarlo con uv o winget.
echo   2. Instalarlo a mano: https://www.python.org/downloads/
echo      Marca "Add python.exe to PATH" e incluye el launcher "py".
echo   3. Si tienes uv:        uv python install %PY_VERSION%
echo   4. Si tienes Python 3.13 o 3.14, no sirve para este proyecto:
echo      las dependencias estan probadas con 3.12.
echo.
echo Diagnostico completo:  run.bat --diag
call :log "ERROR: no hay python %PY_VERSION%"
goto :pausa_error

:version_incorrecta
echo.
echo ERROR: se encontro "%PY_EXE%", pero no es Python %PY_VERSION%.
echo Este proyecto exige 3.12 para evitar conflictos con 3.10 y 3.13.
call :log "ERROR: version incorrecta %PY_EXE%"
goto :pausa_error

:python_32
echo.
echo ERROR: el Python %PY_VERSION% encontrado es de 32 bits.
echo PyTorch no publica paquetes para 32 bits: instala la version 64 bits.
call :log "ERROR: python de 32 bits"
goto :pausa_error

:error_venv
echo.
echo ERROR: no se pudo crear el entorno virtual con "%PY_EXE%".
echo Prueba: cerrar antivirus, mover la carpeta a C:\proyectos y run.bat --reparar
call :log "ERROR: fallo la creacion del .venv"
goto :pausa_error

:error_deps
echo.
echo ERROR: las dependencias no quedaron instaladas correctamente.
echo Revisa el detalle arriba y en %LOG%
call :log "ERROR: dependencias incompletas"
goto :pausa_error

:pausa_error
if not defined MODO_FLAG pause
exit /b 1
