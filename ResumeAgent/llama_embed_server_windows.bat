@echo off
setlocal
title Resume Agent - local embedding server (port 8081)
REM ==========================================================================
REM OPTIONAL semantic search for the Resume Agent.
REM Starts a SECOND, small llama-server with the Qwen3-Embedding-0.6B model.
REM It runs on the CPU (-ngl 0) so your GPU stays free for Qwen; ~1 GB RAM.
REM Your main Qwen server (port 8080) is not touched.
REM
REM First run: if MODEL_PATH does not exist, llama-server downloads the official
REM Qwen/Qwen3-Embedding-0.6B-GGUF (Q8_0, about 0.6 GB) once from Hugging Face.
REM Leave this window open while you use the Resume Agent.
REM ==========================================================================
set "LLAMA_SERVER=D:\llama.cpp\llama-b11269-bin-win-cuda-12.4-x64\llama-server.exe"
set "MODEL_PATH=D:\models\Qwen3-Embedding-0.6B-Q8_0.gguf"
set "HF_MODEL=Qwen/Qwen3-Embedding-0.6B-GGUF:Q8_0"
set "PORT=8081"

if not exist "%LLAMA_SERVER%" (
    echo Cannot find llama-server: "%LLAMA_SERVER%"
    echo Open this BAT file in Notepad and edit LLAMA_SERVER to your llama-server.exe.
    pause
    exit /b 1
)

set "COMMON=--embedding --pooling last --host 127.0.0.1 --port %PORT% -ngl 0 -c 8192 -ub 8192"
if exist "%MODEL_PATH%" (
    echo Starting embedding server with %MODEL_PATH%
    "%LLAMA_SERVER%" -m "%MODEL_PATH%" %COMMON%
) else (
    echo %MODEL_PATH% not found - downloading %HF_MODEL% on first use...
    "%LLAMA_SERVER%" -hf %HF_MODEL% %COMMON%
)
if errorlevel 1 (
    echo.
    echo The embedding server stopped with an error. Check the messages above.
    echo - "unknown argument -hf": your llama.cpp build is too old to download models; download
    echo   Qwen3-Embedding-0.6B-Q8_0.gguf manually and set MODEL_PATH.
    echo - Port in use: change PORT here and the "Embedding server URL" in the app.
)
pause
