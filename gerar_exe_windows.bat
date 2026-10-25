@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo Turing Smart Screen - gerador de executaveis
echo ================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  set PY=python
)
%PY% --version
if errorlevel 1 (
  echo Python 3 nao encontrado. Instale Python 3.10+ e marque Add Python to PATH.
  pause
  exit /b 1
)
%PY% -m pip install --upgrade pyinstaller requests pillow pyserial
if errorlevel 1 goto erro
if not exist build mkdir build
if not exist dist mkdir dist
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name TuringPainel --distpath dist --workpath build\painel --specpath build\spec --hidden-import library.lcd.lcd_comm_rev_a --hidden-import library.lcd.lcd_comm_rev_b --hidden-import serial.tools.list_ports --collect-all serial ha_screen.py
if errorlevel 1 goto erro
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name TuringSmartManager --distpath dist --workpath build\gerenciador --specpath build\spec SmartScreenManager.py
if errorlevel 1 goto erro
echo.
echo Concluido! Os executaveis estao em:
echo   %CD%\dist\TuringSmartManager.exe
echo   %CD%\dist\TuringPainel.exe
echo.
echo Mantenha os dois EXE juntos na pasta dist. O gerenciador salva ha_screen.ini no projeto escolhido.
start "" "%CD%\dist\TuringSmartManager.exe"
exit /b 0
:erro
echo.
echo A compilacao falhou. Confira a mensagem acima e tente novamente.
pause
exit /b 1
