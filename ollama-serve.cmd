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
REM Set on the SERVER: the OpenAI-compatible endpoint accepts keep_alive per
REM request and silently discards it, so config.openai.yaml runs would expire
REM the model on the 5-minute default. Measured - docs/SPEC-DELTA.md C28.
set "OLLAMA_KEEP_ALIVE=30m"
REM The OpenAI protocol has no num_ctx, so a /v1 backend gets whatever the
REM SERVER allocates - 4096 by default, silently, however large
REM usable_context_window says. Verified: without this, n_ctx_slot = 4096
REM against a configured 16384. docs/SPEC-DELTA.md C29.
set "OLLAMA_CONTEXT_LENGTH=16384"

echo Models : %OLLAMA_MODELS%
echo Listen : %OLLAMA_HOST%
echo Keep   : %OLLAMA_KEEP_ALIVE%
echo Ctx    : %OLLAMA_CONTEXT_LENGTH%  (required by config.openai.yaml)
echo.
echo Leave this window open. Ctrl+C to stop.
echo.
ollama serve
