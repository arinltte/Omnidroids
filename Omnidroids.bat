@echo off
REM =====================================================================
REM  Omnidroids one-click launcher (Windows, double-click to run)
REM
REM  Single process (per spec 10): the Flask backend (:5001) serves the built
REM  Vue SPA directly; no Node/Vite dev server at runtime. Only the main page
REM  is opened.
REM
REM  Notes:
REM    - Models are GGUF-only on Windows (MLX is macOS-only).
REM    - All HuggingFace artifacts stay in Omnidroids\.cache\hf (HF_HOME below);
REM      never ~/.cache. Model weights live in Omnidroids\models (git-ignored).
REM    - Stop: close this window or press Ctrl+C. Never `taskkill /f` the
REM      backend (Kuzu WAL corruption).
REM =====================================================================
setlocal enabledelayedexpansion
chcp 65001 >nul

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo ==============================================================
echo [omnidroids] project dir: %ROOT%

REM ---- 1) dependencies (install only when missing) ----------------------
if not exist "%ROOT%\backend\.venv" (
  echo [omnidroids] installing backend deps ^(uv sync^)...
  pushd backend
  call uv sync || echo [ fail ] backend deps failed
  popd
)
if not exist "%ROOT%\frontend\node_modules" (
  echo [omnidroids] installing frontend deps ^(npm install^)...
  pushd frontend
  call npm install || echo [ fail ] frontend deps failed
  popd
)

REM ---- 2) build the SPA only when missing --------------------------------
if not exist "%ROOT%\frontend\dist\index.html" (
  echo [omnidroids] frontend\dist missing - building ^(npm run build^)...
  pushd frontend
  call npm run build || echo [ fail ] frontend build failed
  popd
) else (
  echo [  ok  ] frontend\dist present
)

REM ---- 3) HF artifacts stay inside the folder ----------------------------
set "HF_HOME=%ROOT%\.cache\hf"
echo [omnidroids] HF_HOME=%HF_HOME%

REM ---- 4) start the backend (single process: API + SPA) ------------------
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
netstat -an | findstr /r /c:":5001 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [ warn ] :5001 already in use; skipping start ^(assume running^).
  goto served
)
echo [omnidroids] starting backend on :5001 ^(serves the SPA^)...
start "Omnidroids Backend" /min cmd /c "cd /d %ROOT%\backend && uv run python run.py > %ROOT%\logs\backend.log 2>&1"

echo [omnidroids] waiting for backend...
for /l %%i in (1,1,60) do (
  curl -s --max-time 3 http://localhost:5001/health >nul 2>&1
  if !errorlevel!==0 goto served
  timeout /t 1 /nobreak >nul
)
echo [ fail ] backend not ready - see logs\backend.log

:served
echo ==============================================================
echo [  ok  ] Omnidroids ready
echo    main page : http://localhost:5001
echo    API       : http://localhost:5001/api
echo    monitor   : http://localhost:5001/monitor ^(manual only^)
echo    stop: close this window ^(SIGTERM only; never taskkill /f^)
echo ==============================================================

if /i not "%OPEN_BROWSER%"=="0" (
  start "" http://localhost:5001
)

echo [omnidroids] backend running in its own window. Close it to stop.
pause
