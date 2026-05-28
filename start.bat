@echo off
chcp 65001 >nul
title chat.z.ai Proxy

echo ============================================
echo   chat.z.ai OpenAI Compatible Proxy
echo ============================================
echo.

if not exist .env (
    echo [ERROR] .env file not found!
    echo Run: python extract_env.py your.har .env
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
pip install -r requirements.txt >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] pip install failed, trying to continue anyway...
)

echo [2/3] Checking Playwright browsers...
python -m playwright install chromium >nul 2>&1

echo [3/3] Starting server...
echo.
echo Proxy URL: http://localhost:8000/v1
echo Model: GLM-5.1
echo.
echo Press Ctrl+C to stop
echo.

python server.py

pause
