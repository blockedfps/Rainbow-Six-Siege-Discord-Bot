@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
goto install
:use_python
python -m venv .venv
:install
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist .env copy /y .env.example .env >nul
".venv\Scripts\python.exe" bot.py --check
if errorlevel 1 goto failed
echo.
echo Bereit. Trage Discord-Bot-Token und Arenyze-API-Key in .env ein.
echo Discord-Einrichtung: docs\DISCORD_SETUP.md
echo Danach start.bat ausfuehren.
pause
exit /b 0
:failed
echo.
echo Einrichtung fehlgeschlagen. Python 3.11 oder neuer und Internetzugang pruefen.
pause
exit /b 1
