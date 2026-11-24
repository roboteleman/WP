@echo off
setlocal
cd /d "%~dp0"
echo.
echo === WhatsApp Arsivi - Windows .exe derleme ===
echo.

rem --- Python bul (3.12 / 3.13 / 3.11 tercih edilir) ---
set "PY="
for %%V in (3.12 3.13 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python bulunamadi.
  echo https://www.python.org/downloads/ adresinden Python 3.12 kurun,
  echo kurulumda "Add python.exe to PATH" kutusunu isaretleyin ve bu dosyayi tekrar calistirin.
  pause
  exit /b 1
)
echo Kullanilan Python: %PY%

rem --- Sanal ortam ve paketler ---
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 ( echo Sanal ortam olusturulamadi. & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 ( echo Paketler kurulamadi. Internet baglantinizi kontrol edin. & pause & exit /b 1 )

rem --- Derleme ---
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "WhatsApp Arsivi" ^
  --add-data "wa_arsiv\web;wa_arsiv\web" ^
  --collect-submodules iphone_backup_decrypt ^
  main.py
if errorlevel 1 ( echo Derleme basarisiz oldu. & pause & exit /b 1 )

echo.
echo Bitti!  Program:  dist\WhatsApp Arsivi.exe
echo Bu .exe dosyasini istediginiz bir klasore tasiyabilirsiniz.
echo Arsivler .exe'nin yanindaki "Arsiv_Verisi" klasorunde saklanir.
echo.
pause
