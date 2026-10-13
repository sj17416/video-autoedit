"""바탕화면에 실행 아이콘(바로가기)을 만든다 - Windows 전용.

  python tools/make_shortcut.py            바탕화면에 아이콘 만들기
  python tools/make_shortcut.py --here     이 폴더에도 같이 만들기
  python tools/make_shortcut.py --remove   만든 아이콘 지우기

만드는 아이콘
  영상 자동 편집기      → pythonw launcher.pyw
  인스타 게시물 만들기  → pythonw launcher.pyw /insta
  설치·복구             → 설치.bat (패키지 다시 설치, 아이콘 다시 만들기)

바탕화면 위치는 OneDrive로 옮겨진 경우도 있어서 Windows에게 직접 물어본다.
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = 0x08000000                                        # CREATE_NO_WINDOW
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"

# page가 있으면 pythonw launcher.pyw <page>, bat이 있으면 그 배치 파일을 실행한다
SHORTCUTS = [
    {"name": "영상 자동 편집기", "page": "", "icon": "icon.ico",
     "desc": "영상을 넣으면 컷 편집·자막·개인정보 모자이크까지 자동으로 합니다"},
    {"name": "인스타 게시물 만들기", "page": "/insta", "icon": "icon_insta.ico",
     "desc": "내 사진·영상 장면으로 인스타 게시물을 만들고 예약 업로드합니다"},
    {"name": "영상 편집기 설치·복구", "bat": "설치.bat", "icon": "icon_setup.ico",
     "desc": "필요한 프로그램을 다시 설치하고 바탕화면 아이콘을 다시 만듭니다"},
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
def run_ps(script, extra_env=None):
    """PowerShell 실행.

    -File 대신 -EncodedCommand를 쓴다: 스크립트 '파일'이 아니라서 회사·학교 PC의 스크립트 실행 정책(그룹 정책)에
    막히지 않고, 따옴표·한글 인코딩 문제도 없다. 출력은 UTF-8로 받도록 스크립트 첫 줄에서 지정한다
    (Windows PowerShell 5.1은 기본이 CP949라서 그냥 받으면 한글이 깨진다).
    """
    full = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n" + script
    enc = base64.b64encode(full.encode("utf-16-le")).decode()
    env = {**os.environ, **{k: (v if v != "" else " ") for k, v in (extra_env or {}).items()}}
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", enc], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:500] or "PowerShell 실행 실패")
    return p.stdout.strip()


def pythonw():
    """콘솔 창 없이 실행하는 pythonw.exe.

    Microsoft Store 파이썬은 설치 폴더 이름에 버전이 들어 있어서 업데이트되면 경로가 바뀐다(바로가기가 깨진다).
    그래서 버전이 바뀌어도 그대로인 WindowsApps 별칭을 먼저 쓴다.
    """
    exe = Path(sys.executable)
    if "windowsapps" in str(exe).lower():
        alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "pythonw.exe"
        if alias.exists():
            return alias
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def desktop_dir():
    """바탕화면 폴더. OneDrive로 옮겨졌거나 한글 경로여도 제대로 가져온다.

    PowerShell을 거치지 않고 Windows API를 직접 부른다 (글자 깨짐·정책 문제를 아예 피한다).
    """
    import ctypes
    import uuid
    guid = ctypes.create_string_buffer(uuid.UUID(FOLDERID_DESKTOP).bytes_le, 16)
    out = ctypes.c_wchar_p()
    try:
        ok = ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(out)) == 0
    except OSError:
        ok = False
    if ok:
        path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
        if path.is_dir():
            return path
    import winreg                                            # API가 실패하면 레지스트리에서
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
        path = Path(winreg.QueryValueEx(k, "Desktop")[0])
    if not path.is_dir():
        raise RuntimeError(f"바탕화면 폴더가 없습니다: {path}")
    return path


def make(folders):
    target = pythonw()
    if not target.name.lower().startswith("pythonw"):
        print("※ pythonw.exe를 찾지 못해 python.exe로 만듭니다. 실행할 때 검은 창이 같이 뜰 수 있습니다.")
    made = []
    for sc in SHORTCUTS:
        if "bat" in sc:  # 설치·복구: 진행 상황을 봐야 하므로 검은 창이 있는 배치 파일을 그대로 실행
            exe, args = ROOT / sc["bat"], ""
        else:
            exe, args = target, f'"{ROOT / "launcher.pyw"}"' + (f" {sc['page']}" if sc["page"] else "")
        for folder in folders:
            run_ps(PS_CREATE, {"SC_DIR": str(folder), "SC_NAME": sc["name"], "SC_TARGET": str(exe), "SC_ARGS": args,
                               "SC_WORKDIR": str(ROOT), "SC_ICON": str(ROOT / "assets" / sc["icon"]),
                               "SC_DESC": sc["desc"]})
            made.append(folder / f"{sc['name']}.lnk")
    return made


def remove(folders):
    gone = []
    for sc in SHORTCUTS:
        for folder in folders:
            p = folder / f"{sc['name']}.lnk"
            if p.exists():
                p.unlink()
                gone.append(p)
    return gone


def main():
    if os.name != "nt":
        sys.exit("이 도구는 Windows에서만 쓸 수 있습니다.")
    for icon in {s["icon"] for s in SHORTCUTS}:
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
            print(f"  [완료] {p}")
    except (RuntimeError, OSError) as e:
        sys.exit(f"바로가기를 만들지 못했습니다: {e}")
    print("\n바탕화면에서 아이콘을 더블클릭하면 실행됩니다.")


if __name__ == "__main__":
    main()
