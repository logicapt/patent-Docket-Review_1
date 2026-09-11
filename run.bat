@echo off
title Patent Case Docket & Status Tracker
echo =========================================================
echo   Starting Patent Case Docket & Status Tracker Web App
echo =========================================================
echo.
cd /d "%~dp0"
python -c "import fastapi, uvicorn, playwright, pandas, bs4"
if errorlevel 1 (
    echo Missing dependencies. Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo Browser setup, if needed: python -m playwright install chromium
echo Search and review uses websites with no third-party API keys.
echo Docket text is processed temporarily; the app does not save docket files.
python app.py --open-browser %*
pause
