@echo off
REM Start the DEDICATED Ollama instance this project uses.
REM
REM Why a second instance instead of the default one on 11434:
REM   * its models live in ..\ollama-models, inside the project tree, not in
REM     %USERPROFILE%\.ollama
REM   * your existing models on 11434 are left completely alone
REM
REM Leave this window open while you use the agent. Closing it stops the
REM server, and every local-tier call will fail with a connection refusal.
setlocal
set "HERE=%~dp0"
set "OLLAMA_MODELS=%HERE%..\ollama-models"
set "OLLAMA_HOST=127.0.0.1:11435"

echo Models : %OLLAMA_MODELS%
echo Listen : %OLLAMA_HOST%
echo.
echo Leave this window open. Ctrl+C to stop.
echo.
ollama serve
