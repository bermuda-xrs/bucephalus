@echo off
REM Launcher: runs the CLI straight from src/ without installing the package.
REM
REM Two environment quirks this works around:
REM  1. Editable installs break under a non-ASCII path on a non-UTF-8 ANSI
REM     codepage (setuptools writes its .pth with the locale codec), so we set
REM     PYTHONPATH instead of installing.
REM  2. The venv lives OUTSIDE OneDrive. Its thousands of small files were
REM     deleted twice by an unstable sync; model blobs and source survive fine.
REM     Move it back to .venv here once sync is healthy and it will be found.
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src;%PYTHONPATH%"
set "PYTHONUTF8=1"
if exist "%HERE%.venv\Scripts\python.exe" (
  set "PY=%HERE%.venv\Scripts\python.exe"
) else if exist "%USERPROFILE%\.bucephalus-venv\Scripts\python.exe" (
  set "PY=%USERPROFILE%\.bucephalus-venv\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" -m agent.cli --config "%HERE%config.yaml" %*
