@echo off
rem 블로그 주간 분석 (auto: 예약 실행용, 창을 멈추지 않고 logs\blog_growth.log에 기록)
cd /d "%~dp0.."
set PYTHONUTF8=1
if /i "%~1"=="auto" (
  if not exist logs mkdir logs
  echo ===== %date% %time% >> logs\blog_growth.log
  python -m blog_growth --open >> logs\blog_growth.log 2>&1
  exit /b
)
python -m blog_growth --open
pause
