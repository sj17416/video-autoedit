"""영상 자동 편집기 실행기 — 바로가기 아이콘이 이 파일을 실행한다 (검은 콘솔 창 없음).

1. 서버가 이미 최신 코드로 떠 있으면 창만 연다.
2. 예전 코드의 서버가 떠 있으면(작업 중이 아닐 때) 끄고 새로 띄운다.
3. 앱은 Edge '앱 모드'로 주소창 없는 전용 창에서 열린다.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", 5000))
URL = f"http://127.0.0.1:{PORT}"
LOG = ROOT / "logs" / "server.log"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
EDGE = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe"]


def message(text, error=False):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, text, "영상 자동 편집기", 0x10 if error else 0x40)


def request(path, method="GET", timeout=2.0):
    try:
        req = urllib.request.Request(URL + path, data=b"{}" if method == "POST" else None, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return None, {}


def local_version():
    """app.py의 code_version()과 같은 계산."""
    files = [ROOT / "app.py", *(ROOT / "autoedit").glob("*.py"), *(ROOT / "web").glob("*")]
    return str(int(max(f.stat().st_mtime for f in files if f.is_file())))


def pids_on_port():
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{PORT}") and parts[3] == "LISTENING":
            pids.add(parts[4])
    return pids


def stop_old_server():
    request("/api/shutdown", "POST")  # 새 버전 서버는 스스로 종료
    time.sleep(1.0)
    for pid in pids_on_port():  # 예전 버전 서버(종료 기능 없음)는 프로세스를 끈다 — 파이썬 프로세스만
        info = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                              text=True, creationflags=NO_WINDOW).stdout.lower()
        if "python" in info:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(0.5)


def start_server():
    LOG.parent.mkdir(exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 서버 시작 =====\n")
    log.flush()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    subprocess.Popen([sys.executable, str(ROOT / "app.py"), "--no-browser"], cwd=ROOT, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env)
    for _ in range(120):  # 최대 60초 대기 (첫 실행은 라이브러리 로딩에 시간이 걸림)
        time.sleep(0.5)
        if request("/api/version")[0] == 200:
            return True
    return False


def open_window():
    edge = next((p for p in EDGE if p.exists()), None)
    if edge:
        subprocess.Popen([str(edge), f"--app={URL}", "--window-size=1460,940"], creationflags=NO_WINDOW)
    else:
        webbrowser.open(URL)


def main():
    code, info = request("/api/version")
    if code == 200 and info.get("version") == local_version():
        pass  # 최신 서버가 이미 떠 있음
    else:
        if code is not None:
            busy = request("/api/job")[1].get("running")
            if busy:
                message("이전 버전 앱에서 작업이 진행 중입니다.\n작업이 끝난 뒤 다시 실행하면 새 버전으로 바뀝니다.")
                open_window()
                return
            stop_old_server()
        if not start_server():
            message(f"앱을 시작하지 못했습니다.\n자세한 내용: {LOG}", error=True)
            return
    open_window()


if __name__ == "__main__":
    main()
