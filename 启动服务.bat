@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting local service for A3 paper splitter...
echo Keep this window open while using the web page.
echo.
python serve.py
echo.
echo Service stopped. Press any key to close.
pause >nul
