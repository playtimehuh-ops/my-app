@echo off
REM Run this from the project root: build\build.bat
REM Requires Python 3.10+ installed and on PATH.

setlocal

echo === Creating virtual environment (.venv) ===
python -m venv .venv
call .venv\Scripts\activate.bat

echo === Installing dependencies ===
python -m pip install --upgrade pip
pip install -r requirements.txt

echo === Building AIComputerAgent.exe with PyInstaller ===
pyinstaller build\AIComputerAgent.spec --noconfirm

echo.
echo === Done ===
echo Your app is at: dist\AIComputerAgent\AIComputerAgent.exe
echo (Zip the whole dist\AIComputerAgent folder if you want to move it elsewhere.)

endlocal
pause
