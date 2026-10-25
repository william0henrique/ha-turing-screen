@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo HA Turing Screen - gerador do gerenciador EXE
echo ================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)
%PY% --version
if errorlevel 1 (
  echo Python 3 nao encontrado. Instale Python 3.10+ e marque Add Python to PATH.
  pause
  exit /b 1
)
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto erro
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name TuringSmartManager --distpath dist --workpath build --specpath build SmartScreenManager.py
if errorlevel 1 goto erro
copy /Y ha_screen.py dist\ha_screen.py >nul
if errorlevel 1 goto erro
echo.
echo Concluido: dist\TuringSmartManager.exe
 echo O script do painel foi copiado ao lado do gerenciador.
echo O painel e iniciado sem janela de terminal usando pythonw.exe.
echo Pressione uma tecla para abrir o gerenciador.
pause >nul
start "" "%CD%\dist\TuringSmartManager.exe"
exit /b 0
:erro
echo.
echo A compilacao falhou. Confira a mensagem acima e tente novamente.
pause
exit /b 1
