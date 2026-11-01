@echo off
setlocal
cd /d "%~dp0"
docker compose down
echo Weighthouse stopped. Models and database files were preserved.
pause

