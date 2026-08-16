@echo off
REM Launcher: runs the CLI straight from src/ without installing the package.
REM Editable installs break under a non-ASCII path on a non-UTF-8 ANSI
REM codepage (setuptools writes its .pth with the locale codec), so we set
REM PYTHONPATH instead. PYTHONUTF8 keeps the Greek path readable throughout.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src;%PYTHONPATH%"
set "PYTHONUTF8=1"
if exist "%HERE%.venv\Scripts\python.exe" (
  "%HERE%.venv\Scripts\python.exe" -m agent.cli --config "%HERE%config.yaml" %*
) else (
  python -m agent.cli --config "%HERE%config.yaml" %*
)
