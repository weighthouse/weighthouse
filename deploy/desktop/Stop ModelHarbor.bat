@echo off
setlocal
cd /d "%~dp0"
docker compose down
echo ModelHarbor stopped. Models and database files were preserved.
pause

