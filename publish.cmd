@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY="
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY if exist "C:\Users\szk\AppData\Local\Programs\Python\Python314\python.exe" set "PY=C:\Users\szk\AppData\Local\Programs\Python\Python314\python.exe"
if not defined PY for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"

if not defined PY (
  echo [X] 找不到 Python 解释器。
  echo     装了 Python 的话，把它的路径加到 PATH，或者直接运行:
  echo         python "single line-cad\publish.py"
  pause
  exit /b 1
)

echo 使用 Python: %PY%
echo.
"%PY%" ".\single line-cad\publish.py" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo [完成] 可以关掉这个窗口了。) else (echo [失败] 退出码 %RC%)
pause
endlocal
