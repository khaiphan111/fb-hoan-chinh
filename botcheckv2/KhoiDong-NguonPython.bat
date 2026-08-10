@echo off
chcp 65001 >nul
title FB Live/Die Checker - @nhanxp
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   FB Live/Die Checker  -  Tac gia: @nhanxp
echo   Ho tro: Telegram nhanxp / Facebook nhanxp
echo ============================================
echo.

set PY=
where py >nul 2>&1 && set PY=py
if "!PY!"=="" ( where python >nul 2>&1 && set PY=python )
if "!PY!"=="" (
  echo [*] Khong tim thay Python. Dang cai dat tu dong...
  where winget >nul 2>&1
  if !errorlevel!==0 (
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    set PY=py
  ) else (
    echo [X] May chua co winget. Vui long cai Python 3.12 tai https://python.org roi chay lai.
    pause
    exit /b 1
  )
)
echo [OK] Python: !PY!

cd backend

if not exist static\index.html (
  echo [*] Chua co giao dien dung san. Kiem tra Node.js de build...
  where npm >nul 2>&1
  if !errorlevel!==0 (
    echo [*] Dang build giao dien bang Node.js...
    pushd ..\frontend
    call npm install
    call npm run build
    popd
  ) else (
    echo [!] Khong tim thay Node.js va chua co giao dien build san.
    echo     Tai Node.js tai https://nodejs.org roi chay lai, hoac dung ban .exe.
    pause
    exit /b 1
  )
)

if not exist .venv (
  echo [*] Tao moi truong Python...
  !PY! -m venv .venv
)
call .venv\Scripts\activate.bat
echo [*] Cai thu vien can thiet...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo.
echo [OK] Dang khoi dong... Trinh duyet se tu mo http://127.0.0.1:8000
echo     Mat khau quan tri mac dinh: admin
echo.
python run.py
pause
