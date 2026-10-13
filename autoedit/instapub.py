"""인스타그램 자동 업로드 (Meta 공식 API: Instagram API with Instagram Login).

- 계정마다 앱 대시보드의 'Generate token'으로 받은 60일 토큰을 넣는다. 토큰은 insta/accounts.json(이 PC)에만 저장하고
  50일이 되기 전에 자동으로 연장한다.
- 인스타 서버는 '공개 인터넷 주소'에서 이미지를 가져간다(127.0.0.1의 파일은 못 가져감).
  그래서 올리기 직전에 임시 이미지 호스팅에 올려 주소를 받는다. 이미지가 곧 인스타에 공개되므로 노출 문제는 없다.
- 예약 목록(insta/queue.json)을 30초마다 확인해 시간이 된 게시물을 올린다. 앱(서버)이 켜져 있어야 올라간다.
- 비공식 로그인 봇(아이디·비밀번호로 앱 흉내)은 계정 정지 위험이 커서 쓰지 않는다.
"""
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import insta

def _api():
    """Graph API 주소. 버전은 .env의 INSTA_GRAPH_VERSION으로 바꿀 수 있다 (기본: 문서 예시 버전 v25.0)."""
    return f"https://graph.instagram.com/{os.environ.get('INSTA_GRAPH_VERSION', 'v25.0').strip() or 'v25.0'}"


API = None  # 테스트에서 가짜 서버 주소로 바꿀 때만 쓴다
PUBLISH_DEFAULTS = {
    "auto": True,           # 예약 시간이 되면 자동으로 올리기
    # 임시 이미지 호스팅: litterbox(가입 없음, 1시간 뒤 자동 삭제) / cloudinary(무료 계정, 더 안정적, 올린 뒤 바로 삭제)
    # 하나가 실패하면 다른 쪽으로 자동 전환한다. (imgbb는 약관상 상업적 이용 금지·영구 라이선스라 쓰지 않음)
    "host": "litterbox",
    "cloudinary_url": "",   # Cloudinary 대시보드의 API 환경 변수: cloudinary://<api_key>:<api_secret>@<cloud_name>
}
ACCOUNT_DEFAULTS = {"label": "", "lang": "ko", "daily_max": 10, "start_hour": 9, "end_hour": 23, "enabled": True}
MAX_DAILY = 25              # 너무 자주 올리면 스팸 정책(매우 높은 빈도)에 걸릴 수 있다
REFRESH_AFTER = 7 * 86400   # 토큰을 받은 지 7일이 지나면 연장 (60일 만료 전에 여유 있게)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
PUBLISH_LOCK = threading.Lock()
_stopping = threading.Event()


class IGError(Exception):
    """Meta API 오류. code/subcode로 다시 시도할지 판단한다."""

    def __init__(self, msg, code=None, subcode=None, retry=False, pause=False):
        super().__init__(msg)
        self.code, self.subcode, self.retry, self.pause = code, subcode, retry, pause


# 자주 나는 오류 → 한국어 설명 (재시도 여부, 계정 일시정지 여부). developers.facebook.com 오류 코드 표 기준.
# 같은 상위 code에 여러 뜻이 있어서 세부 코드(error_subcode)를 먼저 본다. 메시지 문장은 믿지 않는다.
SUBCODES = {
    2207052: ("인스타 서버가 이미지를 가져오지 못했습니다. 다음 시도는 다른 이미지 호스팅으로 합니다.", True, False),
    2207003: ("인스타 서버가 이미지를 받는 데 시간이 너무 걸렸습니다. 다음 시도는 다른 이미지 호스팅으로 합니다.", True, False),
    2207020: ("업로드 준비가 만료됐습니다. 다시 시도합니다.", True, False),
    2207001: ("인스타 서버 오류입니다. 잠시 뒤 다시 시도합니다.", True, False),
    2207032: ("인스타가 게시물을 만들지 못했습니다. 잠시 뒤 다시 시도합니다.", True, False),
    2207042: ("오늘 올릴 수 있는 개수(API 한도)를 다 썼습니다. 나중에 다시 시도합니다.", True, False),
    2207051: ("인스타가 스팸으로 의심해 막았습니다. 이 계정의 자동 업로드를 24시간 멈춥니다. 하루 개수를 줄이세요.", False, True),
    2207050: ("계정이 제한(체크포인트) 상태입니다. 인스타 앱에서 확인한 뒤 다시 켜 주세요.", False, True),
    2207027: ("이미지 처리가 아직 끝나지 않았습니다.", True, False),
    2207008: ("업로드 준비 정보가 없어졌습니다. 다시 시도합니다.", True, False),
    2207004: ("이미지 파일이 8MB를 넘습니다.", False, False),
    2207005: ("인스타가 받지 않는 이미지 형식입니다(JPEG만 가능).", False, False),
    2207009: ("이미지 비율이 맞지 않습니다(4:5 ~ 1.91:1).", False, False),
    2207010: ("본문이 너무 깁니다(2,200자 이하).", False, False),
    2207028: ("슬라이드는 2~10장이어야 합니다.", False, False),
}
CODES = {
    190: ("토큰이 만료됐거나 잘못됐습니다. '계정 연결'에서 새 토큰을 넣어 주세요.", False, True),
    9004: SUBCODES[2207052],
    -1: SUBCODES[2207001],
    -2: ("인스타 서버가 일시적으로 응답하지 않습니다. 잠시 뒤 다시 시도합니다.", True, False),
    4: ("요청이 너무 많습니다. 잠시 뒤 다시 시도합니다.", True, False),
    36000: SUBCODES[2207004],
    36001: SUBCODES[2207005],
    36003: SUBCODES[2207009],
    36004: SUBCODES[2207010],
}


def _explain(err):
    code, sub = err.get("code"), err.get("error_subcode")
    found = SUBCODES.get(sub) or CODES.get(code)
    if found:
        msg, retry, pause = found
        return IGError(msg, code, sub, retry, pause)
    detail = err.get("error_user_msg") or err.get("message") or "알 수 없는 오류"
    return IGError(f"인스타 API 오류 ({code}/{sub}): {detail}", code, sub, retry=code in (1, 2, -1, -2))


# ---------------------------------------------------------------- HTTP (표준 라이브러리만 사용)

def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data, mime) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                  f"Content-Type: {mime}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def _http(method, url, params=None, form=None, files=None, timeout=90, raw=False, headers=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers, body = {"User-Agent": "video-autoedit/1.0", **(headers or {})}, None
    if files:
        body, headers["Content-Type"] = _multipart(form or {}, files)
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            err = json.loads(data).get("error")
        except ValueError:
            err = None
        if isinstance(err, dict):
            raise _explain(err) from None
        raise IGError(f"HTTP {e.code} 오류: {data[:200].decode('utf-8', 'replace')}", retry=e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise IGError(f"인터넷 연결 오류: {getattr(e, 'reason', e)}", retry=True) from None
    if raw:
        return data.decode("utf-8", "replace")
    try:
        out = json.loads(data)
    except ValueError:
        raise IGError(f"알 수 없는 응답: {data[:200].decode('utf-8', 'replace')}", retry=True) from None
    if isinstance(out, dict) and isinstance(out.get("error"), dict):
        raise _explain(out["error"])
    return out


def _graph(method, path, token, **params):
    base = API or _api()
    if method == "GET":
        return _http("GET", f"{base}/{path}", params={**params, "access_token": token})
    return _http("POST", f"{base}/{path}", form={**{k: v for k, v in params.items() if v is not None},
                                                "access_token": token})


# ---------------------------------------------------------------- 설정 · 계정

def settings():
    return {**PUBLISH_DEFAULTS, **insta._load("publish.json", {})}


def save_settings(changes):
    with insta.LOCK:
        s = settings()
        for k, v in (changes or {}).items():
            if k == "auto":
                s[k] = bool(v)
            elif k == "host" and v in ("litterbox", "cloudinary"):
                s[k] = v
            elif k == "cloudinary_url" and v:  # 비워 두면 기존 값 유지
                v = str(v).strip().removeprefix("CLOUDINARY_URL=").strip()
                if not _parse_cloudinary(v):
                    raise IGError("Cloudinary 주소 형식이 아닙니다. 대시보드의 'API environment variable' 값"
                                  "(cloudinary://키:비밀@클라우드이름)을 통째로 붙여 넣으세요.")
                s[k] = v
        if s["host"] == "cloudinary" and not s["cloudinary_url"]:
            s["host"] = "litterbox"
        insta._save("publish.json", s)
        return s


def public_settings():
    s = settings()
    c = _parse_cloudinary(s["cloudinary_url"])
    return {**s, "cloudinary_url": f"{c['cloud']} ({mask(c['key'])})" if c else ""}


def mask(token):
    return f"{token[:4]}…{token[-4:]}" if len(token) > 12 else "설정됨"


def accounts():
    return insta._load("accounts.json", [])


def _save_accounts(items):
    insta._save("accounts.json", items)


def public_accounts():
    """화면용: 토큰은 가려서."""
    now = time.time()
    return [{**{k: v for k, v in a.items() if k != "token"}, "token": mask(a["token"]),
             "days_left": max(0, int((a.get("expires_at", 0) - now) // 86400)),
             "paused": a.get("paused_until", 0) > now} for a in accounts()]


def verify_token(token):
    """토큰으로 계정 정보 확인. 반환: {ig_id, username, account_type}"""
    me = _http("GET", f"{API or _api()}/me", params={"fields": "user_id,username,account_type", "access_token": token})
    if isinstance(me.get("data"), list) and me["data"]:  # 문서 예시처럼 data로 감싸 오는 경우
        me = me["data"][0]
    if not me.get("user_id"):
        raise IGError("토큰으로 계정 정보를 읽지 못했습니다. 'Instagram 로그인용 API 설정'에서 만든 토큰인지 확인하세요.")
    return {"ig_id": str(me["user_id"]), "username": me.get("username", ""), "account_type": me.get("account_type", "")}


def add_account(token, fields=None):
    token = (token or "").strip().strip('"').strip("'")
    if len(token) < 20:
        raise IGError("토큰이 너무 짧습니다. 앱 대시보드에서 'Generate token'으로 받은 값을 통째로 붙여 넣으세요.")
    info = verify_token(token)
    if info["account_type"] and info["account_type"].lower() not in ("business", "media_creator", "creator"):
        raise IGError("프로페셔널(크리에이터·비즈니스) 계정만 자동 업로드할 수 있습니다. 인스타 앱 → 프로필 편집 → "
                      "프로페셔널 계정으로 전환을 먼저 해 주세요.")
    with insta.LOCK:
        items = accounts()
        old = next((a for a in items if a["ig_id"] == info["ig_id"]), None)
        acc = {**ACCOUNT_DEFAULTS, **(old or {}), **info, "token": token, "token_at": time.time(),
               "expires_at": time.time() + 60 * 86400}
        acc.pop("paused_until", None)  # 새 토큰을 넣었으면 일시정지·연장 오류 표시는 푼다
        acc.pop("token_error", None)
        acc.setdefault("id", insta._new_id("a"))
        acc["label"] = acc["label"] or f"@{info['username']}"
        for k, v in (fields or {}).items():
            _set_account_field(acc, k, v)
        items = [a for a in items if a["ig_id"] != info["ig_id"]] + [acc]
        _save_accounts(items)
    return acc


def _set_account_field(acc, k, v):
    if k == "lang" and v in insta.instacard.LANGS:
        acc[k] = v
    elif k == "label":
        acc[k] = str(v)[:40]
    elif k == "daily_max":
        acc[k] = max(1, min(MAX_DAILY, int(v)))
    elif k in ("start_hour", "end_hour"):
        acc[k] = max(0, min(24, int(v)))
    elif k == "enabled":
        acc[k] = bool(v)
        if v:
            acc.pop("paused_until", None)


def update_account(acc_id, fields):
    with insta.LOCK:
        items = accounts()
        for a in items:
            if a["id"] == acc_id:
                for k, v in fields.items():
                    _set_account_field(a, k, v)
                if a["end_hour"] <= a["start_hour"]:
                    a["end_hour"] = min(24, a["start_hour"] + 1)
        _save_accounts(items)


def delete_account(acc_id):
    with insta.LOCK:
        _save_accounts([a for a in accounts() if a["id"] != acc_id])
        q = queue()
        for it in q:
            if it["account"] == acc_id and it["status"] == "scheduled":
                it["status"], it["error"] = "canceled", "계정 연결을 삭제했습니다."
        _save_queue(q)


def refresh_tokens(force=False):
    """60일 토큰을 연장. 받은 지 24시간이 지나야 연장할 수 있다."""
    now = time.time()
    updates = {}  # 계정 id → (연장 전 토큰, 바꿀 필드)
    for a in accounts():
        age = now - a.get("token_at", now)
        if age < 86400 or (age < REFRESH_AFTER and not force) or a.get("expires_at", 0) < now:
            continue
        try:
            r = _http("GET", "https://graph.instagram.com/refresh_access_token",
                      params={"grant_type": "ig_refresh_token", "access_token": a["token"]})
            updates[a["id"]] = (a["token"], {"token": r["access_token"], "token_at": now,
                                             "expires_at": now + int(r.get("expires_in", 60 * 86400)),
                                             "token_error": None})
        except (IGError, KeyError) as e:
            updates[a["id"]] = (a["token"], {"token_error": str(e)})
    if not updates:
        return
    with insta.LOCK:  # 그사이 사용자가 새 토큰을 넣었으면 그 계정은 건드리지 않는다
        items = accounts()
        for a in items:
            old_token, fields = updates.get(a["id"], (None, None))
            if fields and a["token"] == old_token:
                for k, v in fields.items():
                    if v is None:
                        a.pop(k, None)
                    else:
                        a[k] = v
        _save_accounts(items)


def publishing_limit(acc):
    """{'used': n, 'total': m} — 24시간 동안 API로 올린 수 / 한도."""
    r = _graph("GET", f"{acc['ig_id']}/content_publishing_limit", acc["token"], fields="quota_usage,config")
    d = (r.get("data") or [{}])[0]
    return {"used": int(d.get("quota_usage", 0)), "total": int((d.get("config") or {}).get("quota_total", 50))}


# ---------------------------------------------------------------- 임시 이미지 호스팅 (인스타가 가져갈 공개 주소)

def _parse_cloudinary(url):
    m = re.fullmatch(r"cloudinary://([^:@\s]+):([^@\s]+)@([\w\-]+)", url or "")
    return {"key": m[1], "secret": m[2], "cloud": m[3]} if m else None


def _cloudinary_sign(params, secret):
    base = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1((base + secret).encode()).hexdigest()


def _upload_cloudinary(jpeg, name):
    c = _parse_cloudinary(settings()["cloudinary_url"])
    if not c:
        raise IGError("Cloudinary 설정이 없습니다.")
    params = {"public_id": f"insta_tmp/{name.rsplit('.', 1)[0]}", "timestamp": str(int(time.time()))}
    r = _http("POST", f"https://api.cloudinary.com/v1_1/{c['cloud']}/image/upload",
              form={**params, "api_key": c["key"], "signature": _cloudinary_sign(params, c["secret"])},
              files={"file": (name, jpeg, "image/jpeg")}, timeout=120)
    if not r.get("secure_url"):
        raise IGError(f"Cloudinary 업로드 실패: {str(r)[:200]}", retry=True)
    return r["secure_url"], {"host": "cloudinary", "public_id": r.get("public_id", params["public_id"])}


def _upload_litterbox(jpeg, name):
    # 브라우저처럼 보이는 헤더가 없으면 412로 거절하는 경우가 있다 (오픈소스 업로더들의 경험)
    url = _http("POST", "https://litterbox.catbox.moe/resources/internals/api.php",
                form={"reqtype": "fileupload", "time": "1h"},
                files={"fileToUpload": (name, jpeg, "image/jpeg")}, raw=True, timeout=120,
                headers={"User-Agent": BROWSER_UA, "Referer": "https://litterbox.catbox.moe/",
                         "Origin": "https://litterbox.catbox.moe"}).strip()
    if not url.startswith("https://"):
        raise IGError(f"litterbox 업로드 실패: {url[:200]}", retry=True)
    return url, {"host": "litterbox"}


HOSTS = {"litterbox": _upload_litterbox, "cloudinary": _upload_cloudinary}


def host_order(flip=False):
    """설정한 호스팅을 먼저, 다른 쪽을 예비로. flip=True면 반대 순서 (인스타가 가져가지 못했을 때)."""
    s = settings()
    order = [s["host"]] + [h for h in HOSTS if h != s["host"]]
    order = [h for h in order if h != "cloudinary" or _parse_cloudinary(s["cloudinary_url"])]
    return order[::-1] if flip and len(order) > 1 else order


def _check_public(url):
    """인스타 서버보다 먼저 직접 받아 본다: 200 + 이미지인지."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = r.status in (200, 206) and r.headers.get("Content-Type", "").startswith("image/")
    except (urllib.error.URLError, TimeoutError, OSError):
        ok = False
    if not ok:
        raise IGError("임시 호스팅에 올린 이미지를 열 수 없습니다.", retry=True)


def host_image(jpeg, name="post.jpg", flip=False):
    """카드 JPEG → 인스타가 가져갈 공개 https 주소. 파일 이름은 영문·숫자만 (한글 주소는 인스타가 거부)."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "", name) or f"{uuid.uuid4().hex}.jpg"
    errors = []
    for host in host_order(flip):
        for attempt in range(2):
            try:
                url, info = HOSTS[host](jpeg, name)
                _check_public(url)
                return url, info
            except IGError as e:
                errors.append(f"{host}: {e}")
                time.sleep(2)
    raise IGError("임시 이미지 호스팅에 올리지 못했습니다 — " + " / ".join(errors[-2:]), retry=True)


def cleanup_hosted(infos):
    """인스타가 이미지를 가져간 뒤 Cloudinary에 남은 사본을 지운다 (litterbox는 1시간 뒤 자동 삭제)."""
    c = _parse_cloudinary(settings()["cloudinary_url"])
    for info in infos:
        if info.get("host") != "cloudinary" or not c:
            continue
        params = {"invalidate": "true", "public_id": info["public_id"], "timestamp": str(int(time.time()))}
        try:
            _http("POST", f"https://api.cloudinary.com/v1_1/{c['cloud']}/image/destroy",
                  form={**params, "api_key": c["key"], "signature": _cloudinary_sign(params, c["secret"])})
        except IGError as e:
            print(f"[인스타] Cloudinary 임시 파일 삭제 실패 (무시): {e}")


# ---------------------------------------------------------------- 예약 목록

def queue():
    return insta._load("queue.json", [])


def _save_queue(items):
    insta._save("queue.json", items)


def _update_item(item_id, **fields):
    with insta.LOCK:
        q = queue()
        for it in q:
            if it["id"] == item_id:
                it.update(fields)
        _save_queue(q)


def queued_post_ids():
    return {pid for it in queue() if it["status"] in ("scheduled", "publishing", "published") for pid in it["posts"]}


def _account_published(acc_id):
    return set(insta._load("history.json", {}).get("by_account", {}).get(acc_id, []))


def _gap(acc):
    return (max(acc["end_hour"], acc["start_hour"] + 1) - acc["start_hour"]) * 3600 / acc["daily_max"]


def _slot_times(acc, n, after):
    """계정의 하루 개수·시간대에 맞춰 n개의 올릴 시각을 고르게 배치.

    하루를 daily_max칸으로 나눈 격자에서 after 이후 칸을 차례로 쓴다 → 하루 개수를 넘지 않고 시간대 밖으로 나가지 않는다.
    칸 안에서 조금씩 흔들어(최대 7분, 칸의 1/4) 기계적인 간격을 피한다.
    """
    gap = _gap(acc)
    lt = time.localtime(after)
    y, m, d = lt.tm_year, lt.tm_mon, lt.tm_mday
    times = []
    for _ in range(400):  # 최대 400일 앞까지
        win0 = time.mktime((y, m, d, acc["start_hour"], 0, 0, 0, 0, -1))
        for i in range(acc["daily_max"]):
            t = win0 + i * gap
            if t >= after:
                times.append(t + random.uniform(0, min(gap * 0.25, 420)))
                if len(times) >= n:
                    return times
        nxt = time.localtime(time.mktime((y, m, d + 1, 12, 0, 0, 0, 0, -1)))  # 다음 날 (월말·서머타임 안전)
        y, m, d = nxt.tm_year, nxt.tm_mon, nxt.tm_mday
    return times


def plan(post_ids, mode="auto", account_id=None, at=None):
    """선택한 게시물을 예약 목록에 넣는다.

    mode="auto": 게시물 언어와 같은 언어로 설정된 계정에 나눠서, 계정의 하루 개수·시간대에 맞춰 자동 배치.
    mode="carousel": 같은 언어 게시물 2~10개를 슬라이드 한 개로 묶어 account_id(없으면 언어 맞는 계정)에 at(없으면 다음 빈 시간).
    반환: (예약된 개수, 건너뛴 이유 목록)
    """
    by_id = {p["id"]: p for p in insta.posts()}
    accs = [a for a in accounts() if a.get("enabled", True)]
    if not accs:
        raise IGError("연결된 인스타 계정이 없습니다. '계정 연결'에서 토큰을 먼저 넣어 주세요.")
    photos = {p["id"]: p for p in insta.photos()}
    skipped, chosen = [], []
    queued = queued_post_ids()
    for pid in post_ids:
        p = by_id.get(pid)
        if not p:
            continue
        if not insta.can_autopost(p):
            skipped.append(f"'{p['title'][:20]}' — 남의 콘텐츠로 보여 제외 (게시물에서 '내 사진 맞음'을 체크하면 예약 가능)")
        elif pid in queued:
            skipped.append(f"'{p['title'][:20]}' — 이미 예약했거나 올린 게시물")
        elif not p["title"].strip() and not p["caption"].strip():
            skipped.append(f"'{pid}' — 제목·본문이 비어 있음")
        else:
            chosen.append(p)
    if not chosen:
        return 0, skipped
    with insta.LOCK:
        q = queue()
        last = {}
        for it in q:
            if it["status"] in ("scheduled", "publishing"):
                last[it["account"]] = max(last.get(it["account"], 0), it["at"])
        new_items = []

        def next_times(acc, n):
            after = max(time.time() + 120, last.get(acc["id"], 0) + _gap(acc) * 0.75)
            ts = _slot_times(acc, n, after)
            last[acc["id"]] = ts[-1]
            return ts

        if mode == "carousel":
            langs = {p["lang"] for p in chosen}
            if len(langs) != 1:
                raise IGError("슬라이드로 묶을 게시물은 모두 같은 언어여야 합니다.")
            if not 2 <= len(chosen) <= 10:
                raise IGError("슬라이드는 2~10장으로 묶을 수 있습니다.")
            acc = next((a for a in accs if a["id"] == account_id), None) or \
                next((a for a in accs if a["lang"] == chosen[0]["lang"]), None)
            if not acc:
                raise IGError(f"{insta.instacard.LANGS[chosen[0]['lang']]} 계정이 없습니다. 계정 연결에서 언어를 지정하세요.")
            when = float(at) if at else next_times(acc, 1)[0]
            new_items.append(_new_item(acc, [p["id"] for p in chosen], when))
        else:
            for lang in dict.fromkeys(p["lang"] for p in chosen):
                group = [p for p in chosen if p["lang"] == lang]
                targets = [a for a in accs if a["lang"] == lang]
                if account_id:
                    targets = [a for a in accs if a["id"] == account_id]
                if not targets:
                    skipped.append(f"{insta.instacard.LANGS[lang]} 게시물 {len(group)}개 — 이 언어로 설정된 계정이 없음")
                    continue
                # 같은 게시물은 한 계정에만 (여러 계정에 같은 글을 올리면 스팸으로 보일 수 있다)
                per = {a["id"]: [] for a in targets}
                for i, p in enumerate(group):
                    acc = targets[i % len(targets)]
                    if photos.get(p["photo"], {}).get("hash") in _account_published(acc["id"]):
                        skipped.append(f"'{p['title'][:20]}' — 이 계정에 이미 올린 사진")
                        continue
                    per[acc["id"]].append(p)
                for acc in targets:
                    items = per[acc["id"]]
                    if not items:
                        continue
                    times = [float(at)] * len(items) if (at and len(items) == 1) else next_times(acc, len(items))
                    for p, t in zip(items, times):
                        new_items.append(_new_item(acc, [p["id"]], t))
        _save_queue(q + new_items)
    return len(new_items), skipped


def _new_item(acc, post_ids, at):
    return {"id": insta._new_id("q"), "account": acc["id"], "posts": post_ids, "at": at, "status": "scheduled",
            "error": "", "tries": 0, "created": time.time()}


def _change(item_id, allowed, **fields):
    """현재 상태가 allowed일 때만 바꾼다 (화면이 몇 초 늦어도 이미 올린 게시물을 다시 올리지 않게)."""
    with insta.LOCK:
        q = queue()
        it = next((i for i in q if i["id"] == item_id), None)
        if not it:
            raise IGError("예약 항목을 찾을 수 없습니다.")
        if it["status"] not in allowed:
            raise IGError({"published": "이미 올린 게시물입니다.", "publishing": "지금 올리는 중입니다."}
                          .get(it["status"], "이 상태에서는 할 수 없습니다.") + " 화면을 새로 고쳐 주세요.")
        it.update(fields)
        _save_queue(q)


def cancel(item_id):
    _change(item_id, ("scheduled",), status="canceled", error="사용자가 취소했습니다.")


def reschedule(item_id, at):
    _change(item_id, ("scheduled", "failed", "canceled"), at=float(at), status="scheduled", error="", tries=0)


def remove(item_id):
    with insta.LOCK:
        _save_queue([it for it in queue() if not (it["id"] == item_id and it["status"] != "publishing")])


def queue_view():
    """화면용 예약 목록 (게시물 제목·계정 이름 포함)."""
    posts = {p["id"]: p for p in insta.posts()}
    accs = {a["id"]: a for a in accounts()}
    out = []
    for it in sorted(queue(), key=lambda i: (i["status"] not in ("scheduled", "publishing"), i["at"])):
        first = posts.get(it["posts"][0], {})
        out.append({**it, "title": first.get("title", "(삭제된 게시물)"), "lang": first.get("lang", ""),
                    "account_label": accs.get(it["account"], {}).get("label", "(삭제된 계정)"),
                    "rev": first.get("rev", 0)})
    return out


# ---------------------------------------------------------------- 올리기

def _wait_ready(acc, container, tries=10):
    """컨테이너가 FINISHED가 될 때까지 잠깐 기다린다 (이미지는 보통 바로 끝남)."""
    for i in range(tries):
        st = _graph("GET", container, acc["token"], fields="status_code,status").get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise IGError(f"인스타가 이미지를 처리하지 못했습니다 ({st}).", retry=st == "EXPIRED")
        time.sleep(3 if i < 3 else 10)
    raise IGError("인스타 이미지 처리가 너무 오래 걸립니다.", retry=True)


def publish_item(item_id):
    """예약 항목 하나를 지금 올린다. 같은 PC에서 동시에 두 개가 올라가지 않게 잠근다."""
    with PUBLISH_LOCK:
        if _stopping.is_set():
            return
        it = next((i for i in queue() if i["id"] == item_id), None)
        if not it or it["status"] not in ("scheduled",):
            return
        acc = next((a for a in accounts() if a["id"] == it["account"]), None)
        posts = [insta.get_post(pid) for pid in it["posts"]]
        if not acc:
            return _update_item(item_id, status="failed", error="계정 연결이 삭제됐습니다.")
        if any(p is None or not insta.card_path(p["id"]).exists() for p in posts):
            return _update_item(item_id, status="failed", error="게시물(카드 이미지)이 삭제됐습니다.")
        if not all(insta.can_autopost(p) for p in posts):
            return _update_item(item_id, status="failed", error="남의 콘텐츠로 보이는 사진이 있어 올리지 않았습니다.")
        _update_item(item_id, status="publishing", started=time.time(), tries=it.get("tries", 0) + 1)
        hosted = []
        try:
            try:
                lim = publishing_limit(acc)
            except IGError as e:
                if e.pause:  # 토큰 문제면 어차피 못 올린다
                    raise
                lim = {"used": 0, "total": 50}  # 조회만 실패 → 그냥 진행 (한도는 인스타가 다시 막아 준다)
            if lim["used"] >= lim["total"]:
                return _update_item(item_id, status="scheduled", at=time.time() + 3600,
                                    error=f"24시간 API 한도({lim['total']}개)를 다 써서 1시간 뒤로 미뤘습니다.")
            # 지난번에 게시 직전·직후에 끊겼으면 같은 컨테이너부터 확인한다 → 이미 올라갔으면 다시 올리지 않음
            container = None
            if it.get("container"):
                try:
                    st = _graph("GET", it["container"], acc["token"], fields="status_code").get("status_code")
                except IGError as e:
                    if e.pause:
                        raise
                    st = None
                if st == "PUBLISHED":
                    _update_item(item_id, status="published", done_at=time.time(), error="",
                                 note="지난번 시도에서 이미 올라가 있었습니다.")
                    _remember(acc["id"], [p["photo"] for p in posts])
                    return
                if st in ("FINISHED", "IN_PROGRESS"):
                    container = it["container"]
            if not container:
                caption = insta.full_caption(posts[0])
                flip = it.get("fetch_fail", 0) % 2 == 1  # 지난번에 인스타가 이미지를 못 가져갔으면 다른 호스팅으로
                hosted = [host_image(insta.card_path(p["id"]).read_bytes(), f"{p['id']}.jpg", flip) for p in posts]
                urls = [u for u, _ in hosted]
                alt = [p["title"].replace("[", "").replace("]", "")[:1000] for p in posts]
                if len(posts) == 1:
                    container = _graph("POST", f"{acc['ig_id']}/media", acc["token"], image_url=urls[0],
                                       caption=caption, alt_text=alt[0])["id"]
                else:
                    children = []
                    for url, a in zip(urls, alt):
                        children.append(_graph("POST", f"{acc['ig_id']}/media", acc["token"], image_url=url,
                                               is_carousel_item="true", alt_text=a)["id"])
                    for c in children:
                        _wait_ready(acc, c)
                    container = _graph("POST", f"{acc['ig_id']}/media", acc["token"], media_type="CAROUSEL",
                                       children=",".join(children), caption=caption)["id"]
                _update_item(item_id, container=container)  # 이 뒤로 끊기면 다음 시도는 이 컨테이너부터 확인
            _wait_ready(acc, container)
            media_id = _graph("POST", f"{acc['ig_id']}/media_publish", acc["token"], creation_id=container)["id"]
            link = ""
            try:
                link = _graph("GET", media_id, acc["token"], fields="permalink").get("permalink", "")
            except IGError:
                pass
            _update_item(item_id, status="published", media_id=media_id, permalink=link, done_at=time.time(), error="")
            cleanup_hosted([info for _, info in hosted])
            _remember(acc["id"], [p["photo"] for p in posts])
            print(f"[인스타] {acc['label']}에 올렸습니다: {posts[0]['title'][:30]} {link}")
        except IGError as e:
            fresh = next((i for i in queue() if i["id"] == item_id), it)
            if e.subcode in (2207003, 2207052) or e.code == 9004:
                _update_item(item_id, fetch_fail=fresh.get("fetch_fail", 0) + 1)
            if e.pause:
                _pause_account(acc["id"])
            if e.subcode == 2207042:  # 24시간 한도: 실패로 세지 않고 1시간 뒤로
                _update_item(item_id, status="scheduled", at=time.time() + 3600, tries=fresh.get("tries", 1) - 1,
                             error=str(e))
            elif e.retry and fresh.get("tries", 0) < 3:
                _update_item(item_id, status="scheduled", at=time.time() + 600 * fresh.get("tries", 1),
                             error=f"{e} (다시 시도 예정)")
            else:
                _update_item(item_id, status="failed", error=str(e))
            print(f"[인스타] 업로드 실패: {e}")
        except Exception as e:  # 예상 못 한 오류도 '올리는 중'에 멈춰 있지 않게
            _update_item(item_id, status="failed", error=f"오류: {e}")
            print(f"[인스타] 업로드 오류: {e}")


def _remember(acc_id, photo_ids):
    for pid in photo_ids:
        insta.mark_published(pid)
    hashes = [p["hash"] for p in insta.photos() if p["id"] in photo_ids]
    with insta.LOCK:
        hist = insta._load("history.json", {})
        lst = hist.setdefault("by_account", {}).setdefault(acc_id, [])
        lst.extend(h for h in hashes if h not in lst)
        insta._save("history.json", hist)


def _pause_account(acc_id):
    with insta.LOCK:
        items = accounts()
        for a in items:
            if a["id"] == acc_id:
                a["paused_until"] = time.time() + 86400
        _save_accounts(items)


# ---------------------------------------------------------------- 예약 실행기 (앱이 켜져 있는 동안)

_lock_file = None
_status = {"running": False, "reason": "", "last_check": 0}


def _acquire_process_lock():
    """이 PC에서 예약 실행기는 한 프로그램만 돌린다 (개발용 서버를 함께 켜도 두 번 올라가지 않게)."""
    global _lock_file
    insta.INSTA.mkdir(exist_ok=True)
    f = open(insta.INSTA / "scheduler.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _lock_file = f
    return True


def _recover():
    """'올리는 중'에 앱이 꺼진 항목: 실제로 올라갔는지 알 수 없으므로 자동으로 다시 올리지 않는다."""
    with insta.LOCK:
        q = queue()
        changed = False
        for it in q:
            if it["status"] == "publishing":
                it["status"] = "failed"
                it["error"] = ("올리는 도중 앱이 꺼졌습니다. 인스타에서 올라갔는지 확인하고, "
                               "안 올라갔으면 '다시 예약'을 누르세요.")
                changed = True
        if changed:
            _save_queue(q)


def _in_window(acc, t):
    lt = time.localtime(t)
    h = lt.tm_hour + lt.tm_min / 60
    return acc["start_hour"] <= h < max(acc["end_hour"], acc["start_hour"] + 1)


def _due(now):
    """지금 올릴 항목. PC가 꺼져 있다 켜져서 밀린 게 많아도 한꺼번에 올리지 않는다:
    계정의 시간대 안에서만, 같은 계정은 최소 간격(칸 간격의 절반, 15분 이상)을 두고 하나씩."""
    accs = {a["id"]: a for a in accounts()}
    q = queue()
    last = {}
    for i in q:
        if i["status"] == "published":
            last[i["account"]] = max(last.get(i["account"], 0), i.get("done_at", 0))
    out = []
    for i in sorted(q, key=lambda i: i["at"]):
        if i["status"] != "scheduled" or i["at"] > now:
            continue
        a = accs.get(i["account"])
        if a is None:
            out.append(i)  # publish_item이 '계정 삭제됨'으로 정리
            continue
        if a.get("paused_until", 0) > now or not a.get("enabled", True) or not _in_window(a, now):
            continue
        if now - last.get(a["id"], 0) < max(900, _gap(a) * 0.5):
            continue
        out.append(i)
    return out


def scheduler_status():
    return {**_status, "auto": settings()["auto"]}


def start_scheduler():
    """예약 실행기 시작. 다른 서버(개발용 등)가 잠금을 잡고 있으면 기다렸다가, 그쪽이 꺼지면 이어받는다."""
    if os.environ.get("INSTA_SCHEDULER", "1") == "0":
        _status["reason"] = "INSTA_SCHEDULER=0 으로 꺼져 있습니다."
        return

    def loop():
        last_refresh = 0
        while not _stopping.is_set():
            try:
                now = time.time()
                if not _status["running"]:
                    if not _acquire_process_lock():
                        _status["reason"] = "다른 창(서버)에서 예약 실행기가 돌고 있습니다. 그쪽이 꺼지면 이어받습니다."
                        time.sleep(30)
                        continue
                    _recover()
                    _status.update(running=True, reason="")
                _status["last_check"] = now
                if now - last_refresh > 6 * 3600:
                    refresh_tokens()
                    last_refresh = now
                if settings()["auto"]:
                    due = _due(now)
                    if due:
                        publish_item(due[0]["id"])
                        continue  # 밀린 게 있으면 바로 다음 것 (간격 규칙은 _due가 지킨다)
            except Exception as e:
                print(f"[인스타 예약] 오류: {e}")
            time.sleep(30)

    threading.Thread(target=loop, daemon=True, name="insta-scheduler").start()


def is_publishing():
    return PUBLISH_LOCK.locked()


def request_stop():
    """앱 종료 요청을 받은 즉시: 새 업로드를 시작하지 않는다."""
    _stopping.set()


def stop(timeout=120):
    """앱 종료 전에: 새 업로드는 시작하지 않고, 올리는 중인 것은 끝날 때까지 기다린다."""
    _stopping.set()
    if PUBLISH_LOCK.acquire(timeout=timeout):
        PUBLISH_LOCK.release()


def publish_now(item_id):
    """'지금 올리기' 버튼: 예약 실행기가 이 프로그램에서 돌 때만 (두 프로그램이 동시에 올리지 않게)."""
    if not _status["running"]:
        raise IGError(f"예약 실행기가 이 창에서 돌고 있지 않습니다: {_status['reason']}")
    _change(item_id, ("scheduled",), at=time.time(), error="", tries=0)
    threading.Thread(target=publish_item, args=(item_id,), daemon=True).start()
