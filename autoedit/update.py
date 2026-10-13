"""자동 업데이트 — GitHub에 올라간 새 버전을 받아서 이 폴더에 덮어쓴다.

왜 필요한가
  사용자는 ZIP으로 받아 쓰기 때문에, 고친 내용을 쓰려면 매번 직접 받아 덮어써야 했다.
  이 모듈이 그 일을 대신한다. 앱이 켜질 때 조용히 확인하고, 화면에서 버튼 한 번으로 받는다.

안전장치
  - ZIP에는 `.gitignore`에 걸린 것(.env, projects/, insta/, config.json, models/, logs/)이 들어있지 않다.
    그래서 덮어써도 API 키·영상 작업 폴더·설정은 사라지지 않는다.
  - 덮어쓰기 전에 지금 파일을 `backup/<날짜>/`에 복사한다 (문제가 생기면 되돌릴 수 있게).
  - 작업(영상 편집·게시물 만들기)이 돌고 있거나 인스타에 올리는 중이면 업데이트하지 않는다.
  - 받은 ZIP이 온전한지(필수 파일이 다 있는지) 먼저 확인하고 나서 덮어쓴다.
"""
import io
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "update.json"          # 마지막으로 받은 버전 (gitignore 대상)
BACKUP = ROOT / "backup"
REPO = os.environ.get("AUTOEDIT_REPO", "sj17416/video-autoedit")
BRANCH = os.environ.get("AUTOEDIT_BRANCH", "main")
API = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
UA = {"User-Agent": "video-autoedit-updater", "Accept": "application/vnd.github+json"}
# ZIP이 온전한지 보는 기준 (이게 다 있어야 덮어쓴다)
REQUIRED = ["app.py", "launcher.pyw", "requirements.txt", "autoedit/pipeline.py", "web/index.html"]
NO_WINDOW = 0x08000000


class UpdateError(RuntimeError):
    """화면에 그대로 보여줄 오류."""


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _get(url, timeout=20):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise UpdateError("GitHub 요청 한도에 걸렸습니다. 한 시간 뒤에 다시 시도해 주세요.") from None
        if e.code == 404:
            raise UpdateError(f"저장소를 찾을 수 없습니다 ({REPO}). 비공개 저장소라면 자동 업데이트를 쓸 수 없습니다.") from None
        raise UpdateError(f"GitHub 오류 {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"인터넷에 연결할 수 없습니다: {getattr(e, 'reason', e)}") from None


# ---------------------------------------------------------------- 지금 버전 / 새 버전 확인

def installed():
    """이 폴더가 지금 어느 버전인지. git으로 받았으면 git에게, ZIP이면 update.json에게 묻는다."""
    if (ROOT / ".git").exists():
        try:
            out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=10, creationflags=NO_WINDOW if os.name == "nt" else 0)
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return (_load(STATE, {}) or {}).get("sha", "")


def check():
    """새 버전이 있는지. 반환: {new, sha, short, date, message, installed}"""
    d = json.loads(_get(API))
    sha = d["sha"]
    msg = (d.get("commit", {}).get("message") or "").splitlines()[0][:120]
    date = d.get("commit", {}).get("committer", {}).get("date", "")
    cur = installed()
    # 지금 버전을 모르면(ZIP으로 처음 받은 경우) 일단 새 버전으로 본다 — 받아도 같은 파일은 건너뛰므로 손해가 없다
    return {"new": cur != sha, "sha": sha, "short": sha[:7], "date": date,
            "message": msg, "installed": cur[:7], "known": bool(cur)}


def _save_state(sha, message=""):
    STATE.write_text(json.dumps({"sha": sha, "message": message, "at": time.time()}, ensure_ascii=False, indent=1),
                     encoding="utf-8")


# ---------------------------------------------------------------- 받아서 덮어쓰기

def _members(zf):
    """ZIP 안의 (폴더를 한 겹 벗긴 상대경로, 압축 항목) 목록."""
    out = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        parts = Path(info.filename).parts
        if len(parts) < 2:
            continue
        rel = Path(*parts[1:])                       # video-autoedit-main/app.py → app.py
        if ".." in rel.parts or rel.is_absolute():   # 압축파일로 폴더 밖에 쓰는 공격 방지
            continue
        out.append((rel, info))
    return out


def apply(sha=None, message="", log=print):
    """최신 ZIP을 받아 이 폴더에 덮어쓴다. 반환: 바뀐 파일 수."""
    log("새 버전을 내려받는 중…")
    data = _get(ZIP_URL, timeout=180)
    log(f"  받기 완료 ({len(data) / 1e6:.1f} MB). 파일을 확인합니다…")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("받은 파일이 깨졌습니다. 잠시 뒤 다시 시도해 주세요.") from None
    with zf:
        members = _members(zf)
        names = {str(rel).replace("\\", "/") for rel, _ in members}
        missing = [f for f in REQUIRED if f not in names]
        if missing:
            raise UpdateError(f"받은 파일이 온전하지 않습니다 (빠진 파일: {', '.join(missing)}). 덮어쓰지 않았습니다.")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        back = BACKUP / stamp
        changed = 0
        log(f"  {len(members)}개 파일을 확인하고 바뀐 것만 바꿉니다…")
        for rel, info in members:
            dest = ROOT / rel
            new = zf.read(info)
            if dest.exists() and dest.read_bytes() == new:
                continue                                   # 같은 파일은 건너뜀
            if dest.exists():                              # 되돌릴 수 있게 백업
                (back / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, back / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".new")
            tmp.write_bytes(new)
            tmp.replace(dest)
            changed += 1
    _save_state(sha or "", message)
    if changed and back.exists():
        log(f"  이전 파일은 backup/{stamp}/ 에 넣어 두었습니다.")
    log(f"업데이트 완료: {changed}개 파일이 바뀌었습니다." if changed else "이미 최신입니다. 바꿀 파일이 없었습니다.")
    return changed


def update(log=print):
    """확인 → 새 버전이면 받아서 덮어쓰기. 반환: {updated, changed, ...}"""
    info = check()
    if not info["new"]:
        log("이미 최신 버전입니다.")
        return {**info, "updated": False, "changed": 0}
    log(f"새 버전이 있습니다: {info['short']} — {info['message']}")
    changed = apply(info["sha"], info["message"], log=log)
    return {**info, "updated": True, "changed": changed}
