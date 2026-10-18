@echo off
REM ==========================================================================
REM OPTIONAL. Only use this if NO llama-server is running yet.
REM If your Research Agent already serves Qwen on 8080, do NOT start a second
REM copy: start_windows.bat finds and reuses the running server.
REM Edit BOTH paths below to your real files (check the exact GGUF filename).
REM ==========================================================================
set "LLAMA_SERVER=D:\llama.cpp\llama-b11269-bin-win-cuda-12.4-x64\llama-server.exe"
set "MODEL_PATH=D:\models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"
set "PORT=8080"

if not exist "%LLAMA_SERVER%" (
    echo Cannot find llama-server: "%LLAMA_SERVER%"
    echo Open this BAT file in Notepad and edit LLAMA_SERVER.
    pause
    exit /b 1
)
if not exist "%MODEL_PATH%" (
    echo Cannot find model: "%MODEL_PATH%"
    echo Open this BAT file in Notepad and edit MODEL_PATH with your exact GGUF filename.
    pause
    exit /b 1
)

REM --ctx-size 16384 : room for long CVs (8192 also works; the app splits work into parts)
REM --jinja          : needed so the app can switch Qwen3 "thinking" off per request
REM --n-cpu-moe 30   : keeps most MoE expert weights in system RAM so a 35B-A3B model fits 8 GB VRAM;
REM                    lower it if you have spare VRAM, raise it if you run out of VRAM
REM --parallel 1     : one request at a time; other apps simply queue
echo Starting llama.cpp on http://127.0.0.1:%PORT%/v1
"%LLAMA_SERVER%" -m "%MODEL_PATH%" --host 127.0.0.1 --port %PORT% --ctx-size 16384 ^
    --n-gpu-layers 99 --n-cpu-moe 30 --parallel 1 --jinja --reasoning-budget 0
if errorlevel 1 (
    echo llama-server stopped with an error. Check the log above.
    echo Out of GPU memory: raise --n-cpu-moe or lower --n-gpu-layers.
    echo Unknown option: your llama.cpp build is older; remove that option or update llama.cpp.
)
pause
