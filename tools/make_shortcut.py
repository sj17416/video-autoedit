"""바탕화면에 실행 아이콘(바로가기)을 만든다 — Windows 전용.

  python tools/make_shortcut.py            바탕화면에 아이콘 2개 만들기
  python tools/make_shortcut.py --here     이 폴더에도 같이 만들기
  python tools/make_shortcut.py --remove   만든 아이콘 지우기

만드는 아이콘
  영상 자동 편집기      → pythonw launcher.pyw
  인스타 게시물 만들기  → pythonw launcher.pyw /insta

바탕화면 위치는 OneDrive로 옮겨진 경우도 있어서 Windows에게 직접 물어본다.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

SHORTCUTS = [
    ("영상 자동 편집기", "", "icon.ico", "영상을 넣으면 컷 편집·자막·개인정보 모자이크까지 자동으로 합니다"),
    ("인스타 게시물 만들기", "/insta", "icon_insta.ico", "내 사진·영상 장면으로 인스타 게시물을 만들고 예약 업로드합니다"),
]

# 바로가기를 만드는 PowerShell 조각. 값은 따옴표 문제가 없도록 환경 변수로 넘긴다.
PS_CREATE = """$ErrorActionPreference = 'Stop'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $env:SC_DIR ($env:SC_NAME + '.lnk')))
$s.TargetPath = $env:SC_TARGET
$s.Arguments = $env:SC_ARGS
$s.WorkingDirectory = $env:SC_WORKDIR
$s.IconLocation = $env:SC_ICON
$s.Description = $env:SC_DESC
$s.Save()
"""
PS_DESKTOP = "[Environment]::GetFolderPath('Desktop')"


def run_ps(script, extra_env=None):
    """PowerShell 실행. 스크립트 파일은 UTF-8 BOM으로 저장해야 한글이 깨지지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sc.ps1"
        path.write_text(script, encoding="utf-8-sig")
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, **(extra_env or {})}, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:500])
    return p.stdout.strip()


def pythonw():
    """콘솔 창 없이 실행하는 pythonw.exe. 없으면 python.exe라도 쓴다."""
    exe = Path(sys.executable)
    for name in ("pythonw.exe", "pythonw"):
        cand = exe.with_name(name)
        if cand.exists():
            return cand
    return exe


def desktop_dir():
    return Path(run_ps(PS_DESKTOP).splitlines()[-1].strip())


def make(folders):
    target = pythonw()
    if not target.name.lower().startswith("pythonw"):
        print("※ pythonw.exe를 찾지 못해 python.exe로 만듭니다. 실행할 때 검은 창이 같이 뜰 수 있습니다.")
    made = []
    for name, page, icon, desc in SHORTCUTS:
        args = f'"{ROOT / "launcher.pyw"}"' + (f" {page}" if page else "")
        for folder in folders:
            run_ps(PS_CREATE, {"SC_DIR": str(folder), "SC_NAME": name, "SC_TARGET": str(target), "SC_ARGS": args,
                               "SC_WORKDIR": str(ROOT), "SC_ICON": str(ROOT / "assets" / icon), "SC_DESC": desc})
            made.append(folder / f"{name}.lnk")
    return made


def remove(folders):
    gone = []
    for name, *_ in SHORTCUTS:
        for folder in folders:
            p = folder / f"{name}.lnk"
            if p.exists():
                p.unlink()
                gone.append(p)
    return gone


def main():
    if os.name != "nt":
        sys.exit("이 도구는 Windows에서만 쓸 수 있습니다.")
    for icon in {s[2] for s in SHORTCUTS}:
        if not (ROOT / "assets" / icon).exists():
            print("아이콘을 먼저 만듭니다…")
            subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
            break
    try:
        folders = [desktop_dir()]
    except (RuntimeError, OSError, IndexError) as e:
        sys.exit(f"바탕화면 폴더를 찾지 못했습니다: {e}")
    if "--here" in sys.argv:
        folders.append(ROOT)
    try:
        if "--remove" in sys.argv:
            gone = remove(folders)
            print("지웠습니다:" if gone else "지울 아이콘이 없습니다.")
            for p in gone:
                print(f"  - {p}")
            return
        for p in make(folders):
            print(f"  ✔ {p}")
    except (RuntimeError, OSError) as e:
        sys.exit(f"바로가기를 만들지 못했습니다: {e}")
    print("\n바탕화면에서 아이콘을 더블클릭하면 실행됩니다.")


if __name__ == "__main__":
    main()
