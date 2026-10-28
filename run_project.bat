@echo off
TITLE QuMail Project Orchestrator
color 0b

echo ===================================================
echo   QuMail Project Quick-Start (1-Command Run)
echo ===================================================

echo [1/3] Checking Frontend Dependencies...
call npm install

echo [2/3] Building Frontend Assets...
call npm run build

echo [3/3] Launching Backend Services...

:: Change to backend directory
pushd backend

echo -- Starting KME Server on port 8443...
start "KME Server (Logs)" cmd /k "python -m uvicorn kme_server:app --host 0.0.0.0 --port 8443"

echo -- Starting Mock DigiLocker on port 8444...
start "Mock DigiLocker (Logs)" cmd /k "python -m uvicorn mock_digilocker.mock_digilocker:app --host 0.0.0.0 --port 8444"

echo -- Starting Main Backend on port 8000...
start "Main Backend (Logs)" cmd /k "python -m uvicorn backend:app --host 127.0.0.1 --port 8000"

:: Return to root
popd

echo ===================================================
echo   [*] Starting Frontend Dashboard...
echo   (Press Ctrl+C to stop the frontend)
echo ===================================================
npm start

pause
