@echo off
setlocal EnableExtensions
title Local Resume and Cover Letter Agent V3 - llama.cpp shared model
cd /d "%~dp0"

rem =============================================================
rem SETTINGS - adapted to your existing Research Agent launcher
rem =============================================================
rem Existing Anaconda installation. Change only if needed.
set "PYTHON=C:\ProgramData\anaconda3\python.exe"
rem auto: check existing llama-server on 8080, then 8090.
rem Or specify a fixed port, e.g. set "LLAMA_PORT=8090".
set "LLAMA_PORT=auto"
rem Other Research Agent commonly uses 8501, so start this on 8502.
set "APP_PORT=8502"
rem OPTIONAL cloud model (OpenRouter). Leave empty to use only the local model.
rem The key stays on this PC; it is only sent to openrouter.ai when you choose
rem "OpenRouter" in the app and tick the consent box.
rem To use it, delete "rem " at the start of the next line and paste your key:
rem set "OPENROUTER_API_KEY=sk-or-v1-paste-your-key-here"
if not defined OPENROUTER_MODEL set "OPENROUTER_MODEL=nvidia/nemotron-nano-9b-v2:free"
rem =============================================================

echo =====================================================
echo   Local Resume + Cover Letter Agent V3 (shared llama.cpp)
echo =====================================================
echo.

if not exist "%~dp0app.py" goto no_app
where curl >nul 2>nul
if errorlevel 1 goto no_curl

rem --- 1. Reuse an ALREADY RUNNING local OpenAI-compatible server.
rem No second GGUF model is loaded by this launcher.
set "API_PORT="
if /I "%LLAMA_PORT%"=="auto" goto detect_port
goto fixed_port

:detect_port
call :ProbeEndpoint 8080
if "%SERVER_READY%"=="1" set "API_PORT=8080"
if defined API_PORT goto model_ready
call :ProbeEndpoint 8090
if "%SERVER_READY%"=="1" set "API_PORT=8090"
if defined API_PORT goto model_ready
goto no_server

:fixed_port
call :ProbeEndpoint %LLAMA_PORT%
if "%SERVER_READY%"=="1" set "API_PORT=%LLAMA_PORT%"
if defined API_PORT goto model_ready
goto no_server

:model_ready
echo Found local model API on port %API_PORT%.
set "LLM_BASE_URL=http://127.0.0.1:%API_PORT%/v1"
echo API endpoint: %LLM_BASE_URL%
echo.

:select_python
rem --- 2. Select Python. Prefer an existing working project venv,
rem then explicitly configured Anaconda, then another Python 3.11+.
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto verify_venv
goto locate_python

:verify_venv
call :HasPackages "%VENV_PY%"
if "%PACKAGES_OK%"=="1" goto use_venv
echo Existing project .venv is incomplete; checking other Python installations...

:locate_python
set "BASE_PY="
if exist "%PYTHON%" call :ProbePython "%PYTHON%"
if defined BASE_PY goto base_ready
if defined CONDA_PREFIX call :ProbePython "%CONDA_PREFIX%\python.exe"
if defined BASE_PY goto base_ready
call :ProbePython python
if defined BASE_PY goto base_ready
call :ProbePython python3
if defined BASE_PY goto base_ready
if exist "%USERPROFILE%\anaconda3\python.exe" call :ProbePython "%USERPROFILE%\anaconda3\python.exe"
if defined BASE_PY goto base_ready
if exist "%USERPROFILE%\miniconda3\python.exe" call :ProbePython "%USERPROFILE%\miniconda3\python.exe"
if defined BASE_PY goto base_ready
if exist "C:\ProgramData\miniconda3\python.exe" call :ProbePython "C:\ProgramData\miniconda3\python.exe"
if defined BASE_PY goto base_ready
where py >nul 2>nul
if errorlevel 1 goto no_python
call :ProbePython py -3
if defined BASE_PY goto base_ready
goto no_python

:base_ready
echo Found Python 3.11+: %BASE_PY%
rem Reuse installed Anaconda packages if all required imports work;
rem do NOT pip-install into your base Anaconda environment.
call :HasPackages %BASE_PY%
if "%PACKAGES_OK%"=="1" goto use_base
if exist "%VENV_PY%" goto install_packages

echo Creating isolated .venv for the Resume Agent...
%BASE_PY% -m venv ".venv"
if errorlevel 1 goto venv_failed

:install_packages
echo Installing Python dependencies inside the project .venv...
"%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto pip_failed
call :HasPackages "%VENV_PY%"
if not "%PACKAGES_OK%"=="1" goto pip_failed
goto use_venv

:use_venv
set "APP_PY="%VENV_PY%""
goto choose_app_port

:use_base
set "APP_PY=%BASE_PY%"
echo Reusing installed Anaconda/Python packages without reinstalling.
goto choose_app_port

:choose_app_port
rem Don't collide with the other Research Agent (usually port 8501).
call :CheckFreePort %APP_PORT%
if "%PORT_FREE%"=="1" goto run_app
if not "%APP_PORT%"=="8502" goto port_busy
echo Port 8502 is in use. Trying 8503...
set "APP_PORT=8503"
call :CheckFreePort %APP_PORT%
if "%PORT_FREE%"=="1" goto run_app
goto port_busy

:run_app
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
echo.
echo Opening Resume Agent at http://127.0.0.1:%APP_PORT%
echo llama.cpp API: %LLM_BASE_URL%
echo Close this window to stop this Streamlit app only.
echo The existing llama-server and other Research Agent are NOT stopped.
echo.
%APP_PY% -m streamlit run app.py --server.address 127.0.0.1 --server.port %APP_PORT% --browser.gatherUsageStats false
if errorlevel 1 goto app_failed
exit /b 0

rem --- Helpers -------------------------------------------------
:ProbeEndpoint
set "SERVER_READY=0"
rem HTTP 200 from /v1/models is required, not just an occupied port.
curl -fsS --max-time 5 -o NUL "http://127.0.0.1:%~1/v1/models" >nul 2>nul
if not errorlevel 1 set "SERVER_READY=1"
exit /b 0

:ProbePython
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASE_PY=%*"
exit /b 0

:HasPackages
set "PACKAGES_OK=0"
%* -c "import sys,streamlit,requests,docx,pypdf,pandas; sys.exit(0 if tuple(int(x) for x in streamlit.__version__.split('.')[:2]) >= (1, 40) else 1)" >nul 2>nul
if not errorlevel 1 set "PACKAGES_OK=1"
exit /b 0

:CheckFreePort
set "PORT_FREE=0"
%APP_PY% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); busy=(s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0); s.close(); sys.exit(1 if busy else 0)" %~1 >nul 2>nul
if not errorlevel 1 set "PORT_FREE=1"
exit /b 0

:no_app
echo [ERROR] app.py is missing. Extract the complete folder before running.
goto fail
:no_curl
echo [ERROR] Windows curl.exe was not found. Install/enable curl or use Windows 10/11.
goto fail
:no_server
echo [WARNING] No local llama.cpp model API answered on the expected port(s).
echo To use your local model: start llama-server and check http://127.0.0.1:8080/v1/models
echo (or set LLAMA_PORT in the SETTINGS block), then restart this launcher.
echo Starting the app anyway: you can choose "OpenRouter" in the app's sidebar.
echo.
set "LLM_BASE_URL=http://127.0.0.1:8080/v1"
goto select_python
:no_python
echo [ERROR] Python 3.11+ was not found.
echo Check C:\ProgramData\anaconda3\python.exe and update PYTHON in SETTINGS.
echo No Windows 'py' launcher is required.
goto fail
:venv_failed
echo [ERROR] Could not create .venv; check Python and write permissions.
goto fail
:pip_failed
echo [ERROR] Dependency installation failed; try from Anaconda Prompt:
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
goto fail
:port_busy
echo [ERROR] Streamlit ports 8502/8503 are in use.
echo Edit APP_PORT in SETTINGS to a free port (e.g. 8504).
goto fail
:app_failed
echo [ERROR] Streamlit stopped unexpectedly. Check the log above.
goto fail
:fail
echo.
pause
exit /b 1
