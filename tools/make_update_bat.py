"""`업데이트.bat`을 다시 만든다 (`tools/update.ps1`을 고친 뒤에 실행).

왜 이렇게 하나
  배치 파일에 PowerShell 코드를 그대로 넣으면 `> | & % ( )` 를 일일이 탈출시켜야 하고,
  한글은 콘솔 코드페이지에 걸려 깨진다. `-EncodedCommand`(UTF-16LE base64)는 둘 다 피해 가고,
  스크립트 실행 정책에도 걸리지 않는다. 읽을 수 있는 원본은 `tools/update.ps1`에 둔다.

이 .bat은 혼자 떨어져 있어도 동작해야 한다. 사용자는 GitHub에서 이 파일 하나만 받아
다운로드 폴더에서 더블클릭한다 — 그래서 설치 폴더는 스스로 찾는다 (update.ps1 참고).
"""
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "tools" / "update.ps1"
BAT = ROOT / "업데이트.bat"

# 배치 부분은 CP949로 저장되므로 `✔ ✖ —` 같은 글자는 쓰면 안 된다 (CLAUDE.md 참고).
TEMPLATE = """@echo off
chcp 949 >nul
title 영상 자동 편집기 - 한번에 업데이트
echo.
echo   ============================================
echo     영상 자동 편집기 : 한번에 업데이트
echo   ============================================
echo.
echo   최신 버전을 받아서 설치 폴더에 덮어씁니다.
echo   사진, 영상, API 키는 그대로 둡니다.
echo.
set "AUTOEDIT_BAT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {b64}
if errorlevel 1 (
  echo.
  echo   [실패] 위의 내용을 그대로 알려 주시면 도와드리겠습니다.
) else (
  echo.
  echo   이제 바탕화면의 '영상 자동 편집기' 또는 '인스타 카드 만들기'를 실행하세요.
)
echo.
pause
"""


def build():
    if not PS1.exists():
        sys.exit(f"[실패] {PS1} 가 없습니다.")
    # -EncodedCommand 는 UTF-16LE base64를 받는다. BOM은 빼야 한다.
    b64 = base64.b64encode(PS1.read_text(encoding="utf-8-sig").encode("utf-16-le")).decode("ascii")
    text = TEMPLATE.format(b64=b64).replace("\n", "\r\n")   # .bat은 CRLF여야 한다
    BAT.write_bytes(text.encode("cp949"))                   # .bat은 CP949여야 한다
    print(f"[완료] {BAT.name} ({BAT.stat().st_size:,} 바이트)")


if __name__ == "__main__":
    build()
