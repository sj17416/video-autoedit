@echo off
rem 매주 월요일 오전 9시에 블로그분석.bat을 실행하도록 Windows 작업 스케줄러에 등록합니다.
rem 그 시간에 PC가 꺼져 있었으면 다음에 켤 때 실행됩니다. 지우려면: schtasks /Delete /TN "블로그 주간 분석" /F
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = New-ScheduledTaskAction -Execute '%~dp0블로그분석.bat' -Argument 'auto'; $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am; $s = New-ScheduledTaskSettingsSet -StartWhenAvailable; Register-ScheduledTask -TaskName '블로그 주간 분석' -Action $a -Trigger $t -Settings $s -Force | Out-Null"
if errorlevel 1 (
  echo 등록에 실패했습니다. 위 오류 메시지를 확인해 주세요.
) else (
  echo 매주 월요일 오전 9시에 블로그 분석이 실행되도록 등록했습니다.
)
pause
