@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  py -3.12 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --name FBLiveChecker ^
  --add-data "static;static" ^
  --collect-all aiogram ^
  --collect-all uvicorn ^
  run.py
echo.
echo Da build xong: dist\FBLiveChecker.exe
pause
