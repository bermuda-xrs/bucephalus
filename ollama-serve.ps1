# Start the dedicated Ollama instance this project uses. See ollama-serve.cmd
# for why it is a second instance rather than the default one on 11434.
#
# Leave this window open while you use the agent.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:OLLAMA_MODELS = Join-Path $here "..\ollama-models"
$env:OLLAMA_HOST = "127.0.0.1:11435"

Write-Host "Models : $env:OLLAMA_MODELS"
Write-Host "Listen : $env:OLLAMA_HOST"
Write-Host ""
Write-Host "Leave this window open. Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
ollama serve
