@echo off
chcp 949 >nul
title 영상 자동 편집기 - 설치
cd /d "%~dp0" 2>nul

rem 파이썬 찾기: 공식 실행기(py)를 먼저, 없으면 python
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto NOPY

rem 경로를 통째로 넘긴다 (네트워크 폴더에서는 cd가 안 되므로 상대 경로를 쓰지 않는다)
%PY% "%~dp0tools\setup.py" %*
echo.
pause
exit /b

:NOPY
echo.
echo   파이썬이 설치돼 있지 않습니다.
echo.
echo   https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행해 주세요.
echo   설치 화면 맨 아래 "Add python.exe to PATH"를 꼭 체크하세요.
echo.
pause
exit /b 1
