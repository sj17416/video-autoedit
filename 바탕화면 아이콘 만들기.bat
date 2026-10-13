@echo off
chcp 949 >nul
echo.
echo  바탕화면에 실행 아이콘을 만듭니다...
echo.
python "%~dp0tools\make_shortcut.py"
echo.
pause
