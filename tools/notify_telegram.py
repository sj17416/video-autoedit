"""텔레그램으로 알림 보내기 (새 쇼츠가 나왔을 때 승인 요청용).

  python tools/notify_telegram.py "메시지" [--video 파일.mp4]

환경변수 TELEGRAM_BOT_TOKEN 이 필요하다. 받는 사람은 TELEGRAM_CHAT_ID, 없으면
봇에게 가장 최근에 말을 건 사람(getUpdates)으로 보내고 그 번호를 알려 준다.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autoedit import env  # noqa: E402
from autoedit.pipeline import ROOT  # noqa: E402


def _api(token, method, data=None, files=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, path in files.items():
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{Path(path).name}\"\r\n"
                     f"Content-Type: {ctype}\r\n\r\n").encode() + Path(path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, json.dumps(data or {}).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("ok"):
        raise SystemExit(f"텔레그램 오류: {res.get('description')}")
    return res["result"]


def chat_id(token):
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if cid:
        return cid
    updates = _api(token, "getUpdates")
    for u in reversed(updates):
        msg = u.get("message") or u.get("edited_message")
        if msg:
            print(f"받는 사람 번호(TELEGRAM_CHAT_ID): {msg['chat']['id']}")
            return msg["chat"]["id"]
    raise SystemExit("받는 사람을 모릅니다. 텔레그램에서 봇에게 아무 말(/start)이나 먼저 보내 주세요.")


def main():
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="텔레그램 알림")
    ap.add_argument("text")
    ap.add_argument("--video", help="같이 보낼 영상 (50MB 이하)")
    args = ap.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN 이 없습니다. 프로젝트 설정의 환경 변수에 넣어 주세요.")
    cid = chat_id(token)
    if args.video:
        _api(token, "sendVideo", {"chat_id": cid, "caption": args.text[:1000], "supports_streaming": "true"},
             {"video": args.video})
    else:
        _api(token, "sendMessage", {"chat_id": cid, "text": args.text})
    print("텔레그램 보냄")


if __name__ == "__main__":
    main()
