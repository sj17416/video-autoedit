"""처음 설치 · 복구 프로그램 - `설치.bat`이 이 파일을 실행한다 (Windows).

하는 일 (순서대로)
  1. 파이썬 버전 확인
  2. 필요한 패키지 설치 (requirements.txt) - 이미 다 있으면 건너뜀
  3. 패키지가 실제로 불러와지는지 확인 (ctranslate2를 가장 먼저 - CLAUDE.md 참고)
  4. 아이콘 만들기
  5. 바탕화면에 실행 아이콘 만들기
  6. API 키 상태를 알려주고 다음에 할 일 안내

언제든 다시 실행해도 됩니다 (복구·업데이트용).
  python tools/setup.py             전체 설치
  python tools/setup.py --update    패키지를 최신으로 올리면서 설치
  python tools/setup.py --no-pip    패키지 설치는 건너뛰기
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_PY = (3, 9)

# (불러올 이름, 보여줄 이름) - ctranslate2가 VC++ 런타임 DLL을 먼저 올려야 뒤의 것들이 로드된다
CHECKS = [
    ("ctranslate2", "음성 인식 엔진"),
    ("flask", "웹앱"),
    ("anthropic", "Claude"),
    ("openai", "OpenAI"),
    ("PIL", "이미지 합성"),
    ("numpy", "계산"),
    ("cv2", "영상 처리"),
    ("imageio_ffmpeg", "ffmpeg"),
    ("faster_whisper", "Whisper"),
    ("rapidocr", "화면 글자 인식"),
    ("onnxruntime", "얼굴·글자 인식 엔진"),
    ("resvg_py", "벡터 그림"),
    ("dotenv", "설정 파일"),
]
STEPS = 5


for _s in (sys.stdout, sys.stderr):  # 한글 콘솔(CP949)로 못 쓰는 글자가 있어도 멈추지 않게
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")


def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say(f"\n[{n}/{STEPS}] {title}")
    say("-" * 46)


def run(args, title):
    """하위 프로세스 실행. 출력은 그대로 보여준다 (설치가 멈춘 것처럼 보이지 않게)."""
    try:
        return subprocess.run(args, cwd=ROOT).returncode == 0
    except OSError as e:
        say(f"  [실패] {title} 실행 실패: {e}")
        return False


def quiet(args):
    """출력을 삼키고 성공 여부만 돌려준다."""
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True).returncode == 0
    except OSError:
        return False


def missing_packages():
    """불러와지지 않는 패키지 목록. 각각 새 프로세스에서 확인한다 (DLL 오류로 이 프로그램이 죽지 않게)."""
    code = "import ctranslate2, sys; __import__(sys.argv[1])"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # 아래에서 utf-8로 읽으므로 자식도 맞춘다
    out = []
    for mod, label in CHECKS:
        p = subprocess.run([sys.executable, "-c", code, mod], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT, env=env)
        if p.returncode != 0:
            reason = (p.stderr or "").strip().splitlines()[-1:] or [""]
            out.append((mod, label, reason[0][:160]))
    return out


def check_python():
    step(1, "파이썬 확인")
    say(f"  버전: {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < MIN_PY:
        say(f"  [실패] 파이썬 {MIN_PY[0]}.{MIN_PY[1]} 이상이 필요합니다. python.org에서 최신 버전을 설치해 주세요.")
        return False
    say("  [완료] 사용할 수 있습니다.")
    return True


def install_packages(update=False):
    step(2, "필요한 패키지 설치")
    req = ROOT / "requirements.txt"
    if not req.exists():
        say(f"  [실패] {req.name} 파일이 없습니다. 프로그램 폴더가 온전한지 확인해 주세요.")
        return False
    if not update and not missing_packages():
        say("  [완료] 이미 다 설치돼 있습니다. (건너뜀)")
        return True
    say("  인터넷에서 받아서 설치합니다. 처음에는 몇 분 걸릴 수 있어요…\n")
    args = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    if update:
        args.insert(4, "--upgrade")
    if not run(args, "pip"):
        say("\n  [실패] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.")
        say("    회사·학교 네트워크라면 방화벽이 막았을 수 있습니다.")
        return False
    return True


def verify():
    step(3, "설치 확인")
    bad = missing_packages()
    for mod, label, reason in bad:
        say(f"  [실패] {label} ({mod}) - {reason}")
    if not bad:
        say(f"  [완료] {len(CHECKS)}개 모두 정상입니다.")
        return True
    if any(m in ("ctranslate2", "cv2", "onnxruntime") for m, _, _ in bad):
        say("\n  ※ 이 오류는 'Microsoft Visual C++ 재배포 가능 패키지(x64)'가 없을 때 자주 납니다.")
        say("    aka.ms/vs/17/release/vc_redist.x64.exe 를 설치한 뒤 이 설치 프로그램을 다시 실행해 주세요.")
    return False


def make_icons():
    step(4, "아이콘 만들기")
    if not quiet([sys.executable, str(ROOT / "tools" / "make_icon.py")]):
        say("  [실패] 아이콘을 만들지 못했습니다. (실행에는 지장이 없습니다)")
        return False
    say("  [완료] 완료")
    return True


def make_shortcuts():
    step(5, "바탕화면에 실행 아이콘 만들기")
    if os.name != "nt":
        say("  (Windows에서만 만들 수 있습니다)")
        return False
    ok = run([sys.executable, str(ROOT / "tools" / "make_shortcut.py")], "바로가기")
    if not ok:
        say("  [실패] 바로가기를 만들지 못했습니다. 프로그램 폴더의 '웹앱실행.bat'으로도 실행할 수 있습니다.")
    return ok


def api_key_status():
    """어떤 키가 들어 있는지만 본다. 키 값은 절대 출력하지 않는다."""
    say("\n" + "=" * 46)
    try:
        sys.path.insert(0, str(ROOT))
        from autoedit import env
        env.load_env(ROOT)
        have = {k: bool(os.environ.get(v)) for k, v in env.KEYS.items()}
    except Exception:
        have = {}
    if have.get("anthropic"):
        say("  [완료] Claude 키가 등록돼 있습니다.")
    else:
        say("  ! Claude 키가 없습니다 - 자막 교정·게시물 글쓰기에 필요합니다.")
        say("    앱을 연 뒤 왼쪽 '설정 (API 키)'에 넣어 주세요.")
    if have.get("openai"):
        say("  [완료] OpenAI 키가 등록돼 있습니다.")
    else:
        say("  ! OpenAI 키는 없어도 됩니다 (그림을 Claude가 그립니다).")


def main():
    say("=" * 46)
    say("  영상 자동 편집기 - 설치 · 복구")
    say("=" * 46)
    say(f"  폴더: {ROOT}")
    if os.name != "nt":
        say("\n  ※ 이 설치 프로그램은 Windows용입니다.")
    update, no_pip = "--update" in sys.argv, "--no-pip" in sys.argv

    if not check_python():
        return 1
    if not no_pip and not install_packages(update):
        return 1
    if no_pip:
        step(2, "필요한 패키지 설치")
        say("  (건너뜀)")
    ready = verify()
    make_icons()
    shortcuts = make_shortcuts()
    api_key_status()

    say("\n" + "=" * 46)
    if ready and shortcuts:
        say("  설치가 끝났습니다.")
        say("  바탕화면의 '영상 자동 편집기' 또는 '인스타 게시물 만들기'를 더블클릭하세요.")
    elif ready:
        say("  설치는 끝났지만 바탕화면 아이콘은 만들지 못했습니다.")
        say("  프로그램 폴더의 '웹앱실행.bat'으로 실행할 수 있습니다.")
    else:
        say("  아직 준비되지 않은 항목이 있습니다. 위의 [실패] 표시를 확인해 주세요.")
        say("  문제를 해결한 뒤 이 설치 프로그램을 다시 실행하면 됩니다.")
    say("=" * 46)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
