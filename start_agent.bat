@echo off
rem Double-click to start the Research Agent: the model server, then the app in your browser.
rem Safe to double-click again: whatever is already running is reused. An app that is running
rem older files (for example after an update) is restarted with the files in this folder.
setlocal EnableExtensions
title Research Agent

rem ============================ SETTINGS: edit to match your PC ============================
rem Best: put your changes in my_settings.bat next to this file (same lines, e.g. set "MODEL=open").
rem It is read after the defaults below, and updates of the app never overwrite it.
rem Full path of llama-server.exe
set "LLAMA_SERVER=D:\llama.cpp\llama-server.exe"
rem Full path of the model file to start, OR the word open to use whichever model server is
rem already running: start llama-server yourself with any model, and the launcher waits for it.
set "MODEL=D:\llama.cpp\models\Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf"
rem Context window in tokens (-c) and CPU threads (-t: use your number of PHYSICAL cores)
set "CTX=65536"
set "THREADS=6"
rem Other llama-server options, as in your usual command
set "LLAMA_ARGS=-ngl 99 --cpu-moe --load-mode none -np 1 -fa on -ctk q8_0 -ctv q8_0 --jinja"
rem Ports for the model server and the app
set "LLAMA_PORT=8080"
set "APP_PORT=8501"
rem Optional: a folder to pre-fill on the Workspace page, e.g. D:\Projects\tyre-study
set "WORKSPACE_DIR="
rem Optional: full path of the python.exe that has the app's packages. Leave empty to search:
rem venv\ or .venv\ next to this file, the Python that owns the "streamlit" command, py, python.
set "PYTHON="
rem ==========================================================================================

cd /d "%~dp0"
if not exist "%~dp0my_settings.bat" call :new_settings
call "%~dp0my_settings.bat"
where curl >nul 2>nul
if errorlevel 1 goto no_curl

rem ---- 1. A Python that has the app's packages. First match wins: the PYTHON setting, venv\ or
rem .venv\ next to this file, the Python that owns the "streamlit" command, then py and python.
set "CHECK=import streamlit, pandas, plotly, requests"
set "PY_OK="
set "TRIED="
if defined PYTHON call :try_python "%PYTHON%"
call :try_python "%~dp0venv\Scripts\python.exe"
call :try_python "%~dp0.venv\Scripts\python.exe"
for /f "delims=" %%G in ('where streamlit 2^>nul') do (
    call :try_python "%%~dpGpython.exe"
    call :try_python "%%~dpG..\python.exe"
)
call :try_python "py"
call :try_python "python"
if not defined PY_OK goto no_streamlit
echo Using Python: %PYTHON%
"%PYTHON%" -c "import sklearn, umap" >nul 2>&1
if errorlevel 1 (
    echo [i] The Patent landscape map needs two more packages. Install them once with:
    echo     "%PYTHON%" -m pip install -r requirements.txt
)

rem ---- 2. App already open? Show it if it runs the files in this folder, otherwise restart it.
call :status "http://127.0.0.1:%APP_PORT%/_stcore/health"
if "%CODE%"=="200" goto app_check
:app_checked

rem ---- 3. Model server: reuse it if running, otherwise start it in its own window.
call :status "http://127.0.0.1:%LLAMA_PORT%/health"
if "%CODE%"=="200" goto model_ready
if /i "%MODEL%"=="open" goto wait_open
if not exist "%LLAMA_SERVER%" goto no_llama
if not exist "%MODEL%" goto no_model

echo Starting the model server in a separate window ...
for %%d in ("%LLAMA_SERVER%") do set "LLAMA_DIR=%%~dpd"
set "LLAMA_DIR=%LLAMA_DIR:~0,-1%"
rem cmd /k keeps that window open if llama-server stops, so its error message stays readable.
start "llama-server - close this window to stop the model" /D "%LLAMA_DIR%" cmd /k ""%LLAMA_SERVER%" -m "%MODEL%" -c %CTX% -t %THREADS% --port %LLAMA_PORT% %LLAMA_ARGS%"

echo Waiting for the model to load. This can take a minute or two.
set /a WAITED=0
:wait_model
timeout /t 2 /nobreak >nul
set /a WAITED+=2
call :status "http://127.0.0.1:%LLAMA_PORT%/health"
if "%CODE%"=="200" goto model_ready
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server.exe" >nul
if errorlevel 1 goto model_died
if %WAITED% geq 900 goto model_slow
<nul set /p "=."
goto wait_model

:wait_open
rem MODEL=open: do not start anything; wait for a model server the user starts.
echo MODEL=open: no model server is answering on port %LLAMA_PORT% yet.
echo Start llama-server now with any model. The app opens as soon as the model is ready.
set /a WAITED=0
:wait_open_loop
timeout /t 2 /nobreak >nul
set /a WAITED+=2
call :status "http://127.0.0.1:%LLAMA_PORT%/health"
if "%CODE%"=="200" goto model_ready
if %WAITED% geq 1800 goto model_slow
<nul set /p "=."
goto wait_open_loop

:model_ready
echo.
echo Model server is ready on port %LLAMA_PORT%. The app uses whichever model it has loaded.

rem ---- 4. The app.
set "LLM_BASE_URL=http://127.0.0.1:%LLAMA_PORT%"
set "LLM_CONTEXT_TOKENS=%CTX%"

rem Skip Streamlit's one-time "enter your email" question, which would otherwise wait here.
if exist "%USERPROFILE%\.streamlit\credentials.toml" goto run_app
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
> "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
>> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""

:run_app
echo.
echo Starting the app. Your browser will open at http://localhost:%APP_PORT%
echo Keep this window open while you use the app. Close it to stop the app.
echo.
"%PYTHON%" -m streamlit run app.py --server.port %APP_PORT% --browser.gatherUsageStats false
if errorlevel 1 goto app_failed
exit /b 0

rem ======================================== helpers ========================================

:try_python
rem Uses %1 as PYTHON if it can import the app's packages. Missing files are skipped quietly.
if defined PY_OK exit /b 0
set "CAND=%~1"
if not "%CAND:\=%"=="%CAND%" if not exist "%CAND%" exit /b 0
set "TRIED=%TRIED% [%CAND%]"
"%CAND%" -c "%CHECK%" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_OK=1"
set "PYTHON=%CAND%"
exit /b 0

:status
rem Sets CODE to the HTTP status of %1, or 000 if nothing answers.
set "CODE=000"
for /f "delims=" %%c in ('curl -s -o nul -m 3 -w "%%{http_code}" %1 2^>nul') do set "CODE=%%c"
exit /b 0

:app_check
rem The app records which files it started with (app_stamp.py); compare with this folder's files.
set "APP_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%APP_PORT% .*LISTENING"') do set "APP_PID=%%p"
"%PYTHON%" "%~dp0app_stamp.py" %APP_PID%
if not errorlevel 1 goto app_running
echo.
echo The app running on port %APP_PORT% was started with older files or from another folder,
echo so it would show the old version. It is restarted now with the files in this folder.
echo An analysis still running in it stops. To keep it instead, close this window now.
timeout /t 10
if not defined APP_PID goto app_stuck
taskkill /pid %APP_PID% /f >nul 2>nul
set /a WAITED=0
:wait_app_stop
call :status "http://127.0.0.1:%APP_PORT%/_stcore/health"
if not "%CODE%"=="200" goto app_checked
timeout /t 1 /nobreak >nul
set /a WAITED+=1
if %WAITED% geq 15 goto app_stuck
goto wait_app_stop

:app_running
echo The app is already running with the files in this folder. Opening it in your browser ...
start "" "http://localhost:%APP_PORT%"
timeout /t 3 >nul
exit /b 0

:new_settings
rem First run: an empty settings file of your own, which updates of the app never overwrite.
> "%~dp0my_settings.bat" echo @rem Your own settings for start_agent.bat and stop_agent.bat.
>> "%~dp0my_settings.bat" echo @rem Copy any "set" line from the SETTINGS block of start_agent.bat here and edit it,
>> "%~dp0my_settings.bat" echo @rem for example remove the @rem in front of the next line to use whichever model is running.
>> "%~dp0my_settings.bat" echo @rem set "MODEL=open"
exit /b 0

:app_stuck
echo.
echo [!] The old app could not be stopped. Double-click stop_agent.bat, then start_agent.bat again.
goto fail

:no_curl
echo [!] This launcher needs curl, which is built into Windows 10 and 11.
echo     Start the model server and the app by hand as described in README.md.
goto fail

:no_llama
echo [!] llama-server was not found at:
echo     %LLAMA_SERVER%
echo     Set the right path in my_settings.bat, next to this file:   set "LLAMA_SERVER=C:\path\to\llama-server.exe"
goto fail

:no_model
echo [!] The model file was not found at:
echo     %MODEL%
echo     Set the right path in my_settings.bat, next to this file:   set "MODEL=C:\path\to\model.gguf"
echo     or set "MODEL=open" there to use whichever model server you start yourself.
goto fail

:model_died
echo.
echo [!] The model server stopped while loading.
echo     Its window shows the reason - often an option it does not recognise, or not enough memory.
echo     Fix LLAMA_ARGS, CTX or THREADS in my_settings.bat, next to this file.
goto fail

:model_slow
echo.
echo [!] The model server did not become ready in time. Check the llama-server window.
goto fail

:no_streamlit
echo [!] Could not find a Python that has the app's packages: streamlit, pandas, plotly, requests.
echo     Tried:%TRIED%
echo.
echo     Fix A: open the terminal where "streamlit run app.py" worked before, activate your
echo            environment as usual, and run:   python -c "import sys; print(sys.executable)"
echo            Put the path it prints into PYTHON in my_settings.bat, next to this file:
echo            set "PYTHON=C:\path\to\python.exe"
echo     Fix B: install the packages once, from this folder:   python -m pip install -r requirements.txt
goto fail

:app_failed
echo.
echo The app has stopped. If you did not stop it yourself, the reason is shown above.
goto fail

:fail
echo.
pause
exit /b 1
