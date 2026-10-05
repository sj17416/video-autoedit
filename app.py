"""유튜브 영상 자동 편집기 — 로컬 웹앱.

  python app.py   →  http://127.0.0.1:5000  (이 PC에서만 접속 가능)
"""
import os
import sys
import warnings

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
warnings.filterwarnings("ignore", module="huggingface_hub")

try:  # ctranslate2(Whisper)가 VC++ 런타임 DLL을 먼저 로드해야 OCR/OpenCV도 정상 동작한다
    import ctranslate2  # noqa: F401
except ImportError as e:
    sys.exit(f"Whisper 엔진 로드 실패: {e}")

import json
import re
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from autoedit import env, jobctl, llm
from autoedit.illustrate import REF_EXTS
from autoedit.pipeline import (PROJECTS, ROOT, STEP_NAMES, STEPS, STYLE_DIR, load_project, new_project_id,
                               output_dir, set_output_dir)
app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 화면 파일이 바뀌면 바로 반영 (옛 화면이 캐시되지 않게)


def code_version():
    """코드 파일들의 최종 수정 시각. 실행기가 '떠 있는 서버가 최신 코드인지' 비교하는 데 쓴다."""
    files = [ROOT / "app.py", *(ROOT / "autoedit").glob("*.py"), *(ROOT / "web").glob("*")]
    return str(int(max(f.stat().st_mtime for f in files if f.is_file())))


VERSION = code_version()


@app.errorhandler(Exception)
def handle_error(e):
    """처리 못 한 오류도 화면에 메시지로 보이도록 JSON으로 돌려준다."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(error=f"{e.code} {e.name}"), e.code
    traceback.print_exc()
    return jsonify(error=f"서버 오류: {e}"), 500


@app.get("/api/version")
def version():
    return jsonify(version=VERSION)


@app.post("/api/shutdown")
def shutdown():
    """앱 종료 (실행기·종료 버튼에서 호출)."""
    jobctl.request()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


# ---------------------------------------------------------------- 작업 실행 (한 번에 하나)

class LogBuffer:
    """print 출력을 모아 웹에 보여준다. '\\r'은 같은 줄 덮어쓰기(진행률)로 처리."""

    def __init__(self):
        self.lines, self.cur, self.lock = [], "", threading.Lock()

    def write(self, s):
        with self.lock:
            for part in re.split(r"(\r|\n)", s):
                if part == "\n":
                    self.lines.append(self.cur)
                    self.cur = ""
                elif part == "\r":
                    self.cur = ""
                else:
                    self.cur += part
            self.lines = self.lines[-500:]

    def flush(self):
        pass

    def text(self):
        with self.lock:
            return "\n".join(self.lines + ([self.cur] if self.cur else []))


JOB = {"running": False, "project": None, "label": "", "error": None, "log": LogBuffer(), "started": 0}
JOB_LOCK = threading.Lock()


def start_job(pid, label, fn):
    with JOB_LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, project=pid, label=label, error=None, log=LogBuffer(), started=time.time())
        jobctl.clear()

    def runner():
        real = sys.stdout
        sys.stdout = JOB["log"]
        try:
            fn()
            print("\n✔ 완료")
        except (llm.UserError, jobctl.Cancelled) as e:
            print(f"\n✖ {e}")
            JOB["error"] = str(e)
        except Exception as e:
            traceback.print_exc(file=JOB["log"])
            JOB["error"] = str(e)
        finally:
            sys.stdout = real
            JOB["running"] = False

    threading.Thread(target=runner, daemon=True).start()
    return True


# ---------------------------------------------------------------- 프로젝트

def project(pid):
    p = load_project(pid) if re.fullmatch(r"[\w\-]+", pid) else None
    if p is None:
        abort(404)
    return p


@app.get("/")
def index():
    return send_from_directory(ROOT / "web", "index.html")


@app.get("/api/projects")
def list_projects():
    out = []
    for d in sorted(PROJECTS.glob("*"), reverse=True) if PROJECTS.exists() else []:
        p = load_project(d.name) if (d / "project.json").exists() else None
        if p is None:
            continue
        size = p.src.stat().st_size / 1e6 if p.src.exists() else 0
        where = "업로드" if p.src.parent == d else str(p.src.parent)
        out.append({"id": d.name, "name": p.name, "info": f"{where} · {size:,.1f} MB",
                    "status": {s: p.done(s) for s in STEPS}})
    return jsonify(out)


@app.post("/api/projects")
def create_project():
    f = request.files.get("video")
    if not f:
        return jsonify(error="영상 파일이 전달되지 않았습니다."), 400
    name = Path(f.filename or "").name or "영상.mp4"  # 파일 이름을 못 읽어도 업로드는 받는다
    pid = new_project_id()
    d = PROJECTS / pid
    d.mkdir(parents=True)
    src = f"source{Path(name).suffix.lower() or '.mp4'}"
    f.save(d / src)
    (d / "project.json").write_text(json.dumps({"name": name, "source": src}, ensure_ascii=False), encoding="utf-8")
    return jsonify({"id": pid})


@app.delete("/api/projects/<pid>")
def delete_project(pid):
    project(pid)
    import shutil
    shutil.rmtree(PROJECTS / pid, ignore_errors=True)  # 작업 파일만 삭제 (완성본·원본 영상은 그대로)
    return jsonify(ok=True)


@app.get("/api/projects/<pid>")
def get_project(pid):
    return jsonify(project(pid).state())


@app.post("/api/projects/<pid>/settings")
def set_settings(pid):
    p = project(pid)
    p.save_settings(request.json)
    return jsonify(p.settings)


@app.post("/api/projects/<pid>/run/<step>")
def run_step(pid, step):
    p = project(pid)
    body = request.json or {}
    if step == "all":
        fn, label = p.run_all, "전체 자동 실행"
    elif step == "proofread":
        fn, label = p.proofread, "자막 교정"
    elif step == "rebuild_subs":
        fn, label = p.rebuild_subtitles, "자막 다시 만들기"
    elif step == "redraw":
        sid = body["scene"]
        fn, label = (lambda: p.illustrate(only=sid)), f"장면 {sid} 다시 그리기"
    elif step == "clear_cache":
        fn, label = p.clear_cache, "캐시 삭제"
    elif step in STEPS:
        def fn():
            # 강제 재실행이면 이 단계(와 이에 의존하는 결과)를 지우고 다시, 아니면 있는 결과를 재사용
            if body.get("force"):
                p.reset(step)
            elif step != "render" and p.done(step):
                print(f"[{STEP_NAMES[step]}] 이미 완료된 결과를 재사용합니다. "
                      "(다시 하려면 '이 단계 강제 재실행'을 체크하세요)")
                return
            getattr(p, step)()  # 앞 단계 결과가 없으면 각 단계가 알아서 먼저 실행
        label = STEP_NAMES[step]
    else:
        abort(404)
    if not start_job(pid, label, fn):
        return jsonify(error="다른 작업이 실행 중입니다."), 409
    return jsonify(ok=True)


@app.post("/api/job/cancel")
def cancel_job():
    jobctl.request()
    return jsonify(ok=True)


@app.post("/api/projects/<pid>/subtitles")
def save_subtitles(pid):
    project(pid).save_subtitles(request.json["cues"])
    return jsonify(ok=True)


@app.get("/api/output-dir")
def get_output_dir():
    return jsonify(path=str(output_dir()))


@app.post("/api/output-dir")
def change_output_dir():
    try:
        return jsonify(path=str(set_output_dir(request.json["path"])))
    except OSError as e:
        return jsonify(error=f"폴더를 쓸 수 없습니다: {e}"), 400


@app.post("/api/open-folder")
def open_folder():
    os.startfile(output_dir())  # 탐색기로 저장 폴더 열기
    return jsonify(ok=True)


@app.get("/api/job")
def job_status():
    return jsonify({k: JOB[k] for k in ("running", "project", "label", "error", "started")} | {"log": JOB["log"].text()})


@app.post("/api/projects/<pid>/scenes")
def save_scenes(pid):
    project(pid).update_scenes(request.json["scenes"])
    return jsonify(ok=True)


@app.post("/api/projects/<pid>/scenes/<sid>/image")
def upload_scene_image(pid, sid):
    project(pid).replace_image(sid, request.files["image"].read())
    return jsonify(ok=True)


@app.get("/api/projects/<pid>/scenes/<sid>/preview")
def scene_preview(pid, sid):
    return app.response_class(project(pid).preview(sid), mimetype="image/jpeg")


@app.post("/api/projects/<pid>/pii")
def save_pii(pid):
    project(pid).update_pii(request.json["off_texts"], request.json["spoken_enabled"])
    return jsonify(ok=True)


@app.get("/api/projects/<pid>/file/<path:name>")
def project_file(pid, name):
    p = project(pid)
    if name.startswith("scenes/"):
        return send_from_directory(p.scenes_dir, name[len("scenes/"):], max_age=0)
    if name in (p.out.name, p.out.with_suffix(".srt").name):
        return send_from_directory(p.out.parent, name, as_attachment=request.args.get("dl") == "1", max_age=0)
    if name == "source":
        return send_from_directory(p.src.parent, p.src.name)
    abort(404)


# ---------------------------------------------------------------- 그림체 참고 이미지 / API 키

@app.get("/api/styles")
def list_styles():
    STYLE_DIR.mkdir(exist_ok=True)
    return jsonify(sorted(p.name for p in STYLE_DIR.glob("*") if p.suffix.lower() in REF_EXTS))


@app.post("/api/styles")
def add_style():
    STYLE_DIR.mkdir(exist_ok=True)
    for f in request.files.getlist("images"):
        name = Path(f.filename).name
        if Path(name).suffix.lower() in REF_EXTS:
            f.save(STYLE_DIR / name)
    return list_styles()


@app.delete("/api/styles/<name>")
def delete_style(name):
    (STYLE_DIR / Path(name).name).unlink(missing_ok=True)
    return list_styles()


@app.get("/api/styles/<name>")
def get_style(name):
    return send_from_directory(STYLE_DIR, Path(name).name)


def mask(key):
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else "설정됨"


@app.get("/api/apikeys")
def apikey_status():
    """키가 있으면 가린 값, 확인 결과(check=1일 때 실제 API로 확인)를 돌려준다."""
    out = {k: {"masked": mask(os.environ[v]) if os.environ.get(v) else None} for k, v in env.KEYS.items()}
    if request.args.get("check"):
        for k, r in env.check_keys().items():
            out[k]["check"] = r
    return jsonify(out)


@app.post("/api/apikeys")
def set_apikeys():
    """비워 둔 칸은 기존 값을 유지한다. 키는 이 PC의 .env 파일에만 저장된다. 저장 후 실제로 확인한다."""
    new = {}
    for k, v in (request.json or {}).items():
        v = env.clean(v)
        if k in env.KEYS and v:
            if "=" in v:  # "OPENAI_API_KEY=sk-..." 처럼 이름까지 붙여 넣은 경우
                v = env.clean(v.split("=", 1)[1])
            new[env.KEYS[k]] = v
    if new:
        path = ROOT / ".env"
        old_names = set(new) | {a for a, real in env.ALIASES.items() if real in new}
        lines = [l for l in (path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else [])
                 if l.split("=", 1)[0].strip() not in old_names]
        path.write_text("\n".join(lines + [f"{k}={v}" for k, v in new.items()]) + "\n", encoding="utf-8")
        os.environ.update(new)
        llm._client = None
    out = {k: {"masked": mask(os.environ[v]) if os.environ.get(v) else None} for k, v in env.KEYS.items()}
    for k, r in env.check_keys().items():
        out[k]["check"] = r
    return jsonify(out)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    PROJECTS.mkdir(exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    print(f"웹앱 실행 중: http://127.0.0.1:{port}  (이 창을 닫으면 종료됩니다)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, threaded=True)
