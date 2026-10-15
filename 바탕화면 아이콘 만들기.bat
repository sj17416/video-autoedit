@echo off
chcp 949 >nul
title 바탕화면 아이콘 만들기
cd /d "%~dp0" 2>nul
echo.
echo   바탕화면에 실행 아이콘을 만듭니다...
echo.
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   파이썬을 찾지 못했습니다. '설치.bat'을 먼저 실행해 주세요.
  echo.
  pause
  exit /b 1
)
%PY% "%~dp0tools\make_shortcut.py" %*
echo.
pause
