# Start the dedicated Ollama instance this project uses. See ollama-serve.cmd
# for why it is a second instance rather than the default one on 11434.
#
# Leave this window open while you use the agent.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:OLLAMA_MODELS = Join-Path $here "..\ollama-models"
$env:OLLAMA_HOST = "127.0.0.1:11435"

# Set on the SERVER because the OpenAI-compatible endpoint cannot set it per
# request: /v1 accepts `keep_alive` and silently discards it, so a config.openai
# run would expire the model after the 5-minute default no matter what the
# request said. Measured 2026-09-06, Ollama 0.33.3 - see docs/SPEC-DELTA.md C28.
# On CPU a cold load is ~30s against ~8s warm, so this is wall clock, which is
# the budget that actually binds here.
$env:OLLAMA_KEEP_ALIVE = "30m"

# Same reason, and this one is worse if it is missed. The OpenAI protocol has
# no num_ctx, so a /v1 backend gets whatever the SERVER allocates - 4096 by
# default, silently, however large usable_context_window says it is. M3.4
# budgets against usable_context_window, so a mismatch means the ceiling math
# is a lie and the agent loses the head of its own context mid-task with no
# error. Verified 2026-09-06: without this, n_ctx_slot = 4096 against a
# configured 16384; with it, 16384. docs/SPEC-DELTA.md C29.
$env:OLLAMA_CONTEXT_LENGTH = "16384"

Write-Host "Models : $env:OLLAMA_MODELS"
Write-Host "Listen : $env:OLLAMA_HOST"
Write-Host "Keep   : $env:OLLAMA_KEEP_ALIVE"
Write-Host "Ctx    : $env:OLLAMA_CONTEXT_LENGTH  (required by config.openai.yaml)"
Write-Host ""
Write-Host "Leave this window open. Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
ollama serve
