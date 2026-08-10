@echo off
chcp 65001 >nul
title TikTok Checker V2 — @khaikhai998
echo.
echo  ╔══════════════════════════════════════╗
echo  ║   TikTok Checker V2 — @khaikhai998  ║
echo  ╚══════════════════════════════════════╝
echo.
echo  Dang kiem tra thu vien...
pip install -r backend\requirements.txt -q
echo.
echo  Khoi dong server tai http://localhost:8899
echo.
python run.py
pause
