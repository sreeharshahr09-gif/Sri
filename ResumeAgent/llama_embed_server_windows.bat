@echo off
setlocal
title Resume Agent - local embedding server (port 8081)
REM ==========================================================================
REM OPTIONAL semantic search for the Resume Agent.
REM Starts a SECOND, small llama-server with the Qwen3-Embedding-0.6B model.
REM It runs on the CPU (-ngl 0) so your GPU stays free for Qwen; ~1 GB RAM.
REM Your main Qwen server (port 8080) is not touched.
REM
REM MODEL_PATH must be the small EMBEDDING model, never your 35B chat model.
REM If the file is missing, it is downloaded once (639 MB) with Windows curl.exe,
REM which uses the Windows certificate store (works behind most company proxies).
REM Leave this window open while you use the Resume Agent.
REM ==========================================================================
set "LLAMA_SERVER=D:\llama.cpp\llama-b11269-bin-win-cuda-12.4-x64\llama-server.exe"
set "MODEL_PATH=D:\llama.cpp\models\qwen3-embedding\Qwen3-Embedding-0.6B-Q8_0.gguf"
set "MODEL_URL=https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf"
set "PORT=8081"

if not exist "%LLAMA_SERVER%" goto no_server
if exist "%MODEL_PATH%" goto start_server

echo Embedding model not found at:
echo   %MODEL_PATH%
echo Downloading it once from Hugging Face - about 639 MB...
for %%I in ("%MODEL_PATH%") do if not exist "%%~dpI" mkdir "%%~dpI"
curl.exe -L --fail --retry 3 -o "%MODEL_PATH%.part" "%MODEL_URL%"
if errorlevel 1 goto download_failed
for %%I in ("%MODEL_PATH%.part") do if %%~zI LSS 100000000 goto download_failed
move /Y "%MODEL_PATH%.part" "%MODEL_PATH%" >nul
echo Download complete.
echo.

:start_server
echo Starting embedding server on http://127.0.0.1:%PORT%  -  leave this window open.
"%LLAMA_SERVER%" -m "%MODEL_PATH%" --embedding --pooling last --host 127.0.0.1 --port %PORT% -ngl 0 -c 8192 -ub 8192
if errorlevel 1 goto server_failed
goto end

:no_server
echo Cannot find llama-server: "%LLAMA_SERVER%"
echo Open this BAT file in Notepad and set LLAMA_SERVER to your llama-server.exe.
goto end

:download_failed
if exist "%MODEL_PATH%.part" del "%MODEL_PATH%.part"
echo.
echo [ERROR] The automatic download did not work.
echo Download it in your web browser instead:
echo   1. Open https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/tree/main
echo   2. Click Qwen3-Embedding-0.6B-Q8_0.gguf, then the download button - 639 MB.
echo   3. Save it as: %MODEL_PATH%
echo   4. Run this file again.
goto end

:server_failed
echo.
echo The embedding server stopped with an error. Check the messages above.
echo - Port in use: change PORT here and the Embedding server URL in the app sidebar.
echo - Model error: delete the .gguf file and run this again to re-download it.

:end
pause
