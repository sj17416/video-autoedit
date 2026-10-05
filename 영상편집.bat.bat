@echo off
if "%~1"=="" (
  echo 편집할 영상 파일을 이 아이콘 위로 끌어다 놓으세요. 여러 개도 됩니다.
  pause
  exit /b
)
python "%~dp0main.py" %*
pause
