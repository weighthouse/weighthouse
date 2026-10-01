@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" copy /Y ".env.example" ".env" >nul
echo Starting ModelHarbor...
docker compose up --build -d
if errorlevel 1 (
  echo.
  echo ModelHarbor could not start. Make sure Docker Desktop is running.
  pause
  exit /b 1
)
echo.
echo ModelHarbor is starting at http://localhost:7860
start "" "http://localhost:7860"

