"""인스타그램 게시물 만들기: 내 사진·내 영상 장면 → 언어별 후킹 제목·본문(Claude) → 카드 이미지(PIL, 0원).

저장 위치: insta/ (사진·카드·설정·예약 목록. .gitignore 대상)
- 사진은 '내가 찍은 사진'과 '내 영상(프로젝트)에서 뽑은 장면'만 쓰도록 만든다.
  남의 게시물 캡처는 글씨·그라데이션을 바꿔도 2차 저작물이라 저작권 문제를 피할 수 없다.
  Claude가 캡처·워터마크로 보이는 사진을 표시하고, 표시된 사진은 확인 전까지 자동 업로드하지 않는다.
- 같은(거의 같은) 사진은 지문(dHash)으로 걸러서 두 번 올리지 않는다.
"""
import base64
import io
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import instacard, jobctl, llm, media

ROOT = Path(__file__).resolve().parent.parent
INSTA = ROOT / "insta"
PHOTOS = INSTA / "photos"
CARDS = INSTA / "cards"
LOGO = INSTA / "logo.png"
PHOTO_EXTS = {".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp", ".bmp", ".heic", ".heif"}
DUP_DISTANCE = 5        # 지문 비트 차이가 이 이하면 같은 사진으로 본다
BATCH = 6               # Claude에 한 번에 보내는 사진 수
MAX_HASHTAGS = 5        # 인스타는 2025년 12월부터 게시물당 해시태그 5개까지
LOCK = threading.RLock()  # 웹 요청·작업·예약 스레드가 같은 파일을 동시에 고치지 않게

SETTINGS_DEFAULTS = {
    "brand": "",            # 로고 이미지가 없을 때 위에 쓸 브랜드 이름 (예: 구름빵태양)
    "langs": ["ko"],        # 만들 언어
    "ratio": "1:1",         # 1:1 / 4:5
    "topic": "",            # 계정 주제 (예: 이집트 여행)
    "style": "",            # 작성 예시 (제목·본문 형식)
    "hashtags": 0,          # 해시태그 개수 (0이면 넣지 않음)
    **instacard.CARD_DEFAULTS,
}


def _retry(fn, tries=20):
    """Windows에서는 다른 스레드가 파일을 읽는 순간 교체·열기가 잠깐 막힐 수 있다 → 잠시 뒤 다시."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


def _load(name, default):
    p = INSTA / name
    if not p.exists():
        return default
    try:
        return json.loads(_retry(lambda: p.read_text(encoding="utf-8")))
    except ValueError:
        # 빈 목록으로 보고 계속하면 다음 저장 때 내용이 통째로 지워진다 → 멈추고 알린다
        raise llm.UserError(f"{p} 파일이 손상됐습니다. 백업이 있으면 되돌리거나 파일을 지운 뒤 다시 시도하세요.") from None


def _save(name, data):
    INSTA.mkdir(exist_ok=True)
    p = INSTA / name
    tmp = p.with_name(f"{p.stem}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    _retry(lambda: tmp.replace(p))  # 쓰다가 꺼져도 원래 파일이 깨지지 않게


def _new_id(prefix):
    return f"{prefix}{time.strftime('%y%m%d%H%M%S')}{uuid.uuid4().hex[:4]}"


def safe_id(s):
    return bool(re.fullmatch(r"[\w\-]+", s or ""))


# ---------------------------------------------------------------- 설정 · 로고

def settings():
    s = {**SETTINGS_DEFAULTS, **_load("settings.json", {})}
    s["langs"] = [l for l in s["langs"] if l in instacard.LANGS] or ["ko"]
    return s


def save_settings(changes):
    with LOCK:
        s = settings()
        for k, v in (changes or {}).items():
            if k not in SETTINGS_DEFAULTS:
                continue
            d = SETTINGS_DEFAULTS[k]
            if k == "langs":
                v = [l for l in v if l in instacard.LANGS] or ["ko"]
            elif isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, (int, float)):
                v = type(d)(v)
            else:
                v = str(v)
            s[k] = v
        if s["ratio"] not in instacard.RATIOS:
            s["ratio"] = "1:1"
        if s["logo_pos"] not in ("left", "center", "right"):
            s["logo_pos"] = "center"
        s["hashtags"] = max(0, min(MAX_HASHTAGS, int(s["hashtags"])))
        _save("settings.json", s)
        return s


def set_logo(data):
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    bbox = img.getchannel("A").getbbox()  # 투명 여백 잘라내기
    if bbox:
        img = img.crop(bbox)
    img.thumbnail((1200, 400), Image.LANCZOS)
    INSTA.mkdir(exist_ok=True)
    img.save(LOGO, "PNG")


def remove_logo():
    LOGO.unlink(missing_ok=True)


def _logo():
    return Image.open(LOGO).convert("RGBA") if LOGO.exists() else None


# ---------------------------------------------------------------- 사진

def photos():
    return _load("photos.json", [])


def _check_id(x):
    if not safe_id(x):  # Windows에서는 역슬래시(..\\)로 폴더 밖 파일을 가리킬 수 있다
        raise llm.UserError("잘못된 id입니다.")
    return x


def photo_path(pid):
    return PHOTOS / f"{_check_id(pid)}.jpg"


def _published_hashes():
    """이미 인스타에 올라간 사진 지문 (중복 업로드 방지)."""
    return {h for h in _load("history.json", {}).get("hashes", [])}


def add_photo_image(img, name, source="upload", note=""):
    """사진 한 장 추가. 이미 있는 사진과 거의 같으면 추가하지 않고 그 사진 id를 돌려준다."""
    h = instacard.dhash(img)
    with LOCK:
        items = photos()
        for p in items:
            if instacard.hash_distance(h, p["hash"]) <= DUP_DISTANCE:
                return p["id"], False
        pid = _new_id("p")
        PHOTOS.mkdir(parents=True, exist_ok=True)
        img.save(photo_path(pid), "JPEG", quality=92)
        items.append({"id": pid, "name": name, "note": note, "hash": h, "source": source,
                      "w": img.width, "h": img.height, "added": time.time()})
        _save("photos.json", items)
        return pid, True


def add_uploads(files):
    """files: [(파일이름, bytes)] → (추가된 수, 중복으로 건너뛴 수, 못 읽은 파일 이름들)"""
    added = dup = 0
    bad = []
    for name, data in files:
        if Path(name).suffix.lower() not in PHOTO_EXTS:
            continue
        try:
            img = instacard.load_photo(data)
        except Exception:
            bad.append(Path(name).name)  # 아이폰 HEIC는 PIL이 못 읽는다 → 화면에서 '높은 호환성' 설정을 안내
            continue
        _, new = add_photo_image(img, Path(name).name)
        added, dup = added + new, dup + (not new)
    return added, dup, bad


def update_photo(pid, note):
    with LOCK:
        items = photos()
        for p in items:
            if p["id"] == pid:
                p["note"] = str(note)[:500]
        _save("photos.json", items)


def delete_photo(pid):
    with LOCK:
        _save("photos.json", [p for p in photos() if p["id"] != pid])
        photo_path(pid).unlink(missing_ok=True)
        for post in [p for p in posts() if p["photo"] == pid]:
            delete_post(post["id"])


def photo_list():
    """화면용: 사진 + 이미 올린 사진인지 표시."""
    published = _published_hashes()
    return [{**p, "published": any(instacard.hash_distance(p["hash"], h) <= DUP_DISTANCE for h in published)}
            for p in photos()]


# ---------------------------------------------------------------- 내 영상에서 장면 뽑기

def _frame_at(src, t, max_side=1920):
    png = subprocess.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
                          "-frames:v", "1", "-vf", f"scale='min({max_side},iw)':-2", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB") if png else None


def import_frames(project, count=8):
    """프로젝트 원본 영상에서 선명하고 서로 다른 장면 count장을 사진으로 추가.

    컷 편집을 했으면 잘려나간 구간(무음·추임새)은 건너뛴다. 자막·효과가 없는 깨끗한 원본 프레임을 쓴다.
    """
    meta = project.meta
    keep = (json.loads(project.p("cutplan.json").read_text(encoding="utf-8"))["keep"]
            if project.p("cutplan.json").exists() else [[0, meta["duration"]]])
    total = sum(e - s for s, e in keep)
    if total <= 0:
        raise llm.UserError("영상 길이를 읽을 수 없습니다.")
    n_cand = min(60, max(count * 4, 12))
    times = []
    for i in range(n_cand):  # 남은 구간 위에 고르게
        x = total * (i + 0.5) / n_cand
        for s, e in keep:
            if x <= e - s:
                times.append(s + x)
                break
            x -= e - s
    print(f"영상에서 후보 장면 {len(times)}개를 읽는 중…")
    cands = []
    for i, t in enumerate(times):
        jobctl.check()
        img = _frame_at(project.src, t)
        if img is not None:
            cands.append((instacard.sharpness(img), t, img, instacard.dhash(img)))
        print(f"\r  {i + 1}/{len(times)}", end="", flush=True)
    print()
    # 선명한 순으로 고르되, 서로 비슷한 장면(같은 구도)은 하나만
    # 같은 구도가 많은 영상(말하는 영상 등)이면 기준을 점점 낮춰서 요청한 장수를 채운다
    good = [c for c in sorted(cands, key=lambda c: -c[0]) if c[0] >= 15]  # 심하게 흔들리거나 까만 화면 제외
    picked = []
    for min_diff in (12, 8, DUP_DISTANCE):  # 마지막 단계도 '같은 사진' 기준보다는 달라야 한다
        for c in good:
            if len(picked) >= count:
                break
            if all(c[1] != p[1] and instacard.hash_distance(c[3], p[3]) > min_diff for p in picked):
                picked.append(c)
    added = 0
    for score, t, img, h in sorted(picked, key=lambda c: c[1]):
        _, new = add_photo_image(img, f"{project.name} {int(t // 60)}분 {t % 60:04.1f}초",
                                 source=f"video:{project.name}@{t:.1f}")
        added += new
    print(f"장면 {added}장을 사진 목록에 추가했습니다." + (f" (이미 있는 장면 {len(picked) - added}장 제외)"
                                                  if len(picked) > added else ""))
    return added


# ---------------------------------------------------------------- 게시물

def posts():
    return _load("posts.json", [])


def card_path(post_id):
    return CARDS / f"{_check_id(post_id)}.jpg"


def get_post(post_id):
    return next((p for p in posts() if p["id"] == post_id), None)


def _save_post(post, if_rev=None):
    """if_rev: 합성을 시작할 때의 rev. 그사이 사용자가 고치거나 지웠으면 저장하지 않는다 (오래된 내용으로 덮어쓰기 방지)."""
    with LOCK:
        cur = posts()
        if if_rev is not None:
            now = next((p for p in cur if p["id"] == post["id"]), None)
            if now is None or now.get("rev", 0) != if_rev:
                return False
        items = [p for p in cur if p["id"] != post["id"]]
        items.append(post)
        items.sort(key=lambda p: (p["created"], p["id"]))
        _save("posts.json", items)
        return True


def render_card(post, s=None):
    """게시물 카드 이미지를 (다시) 합성해서 저장. 내 PC에서 바로 끝난다 (비용 0원)."""
    s = s or settings()
    opts = {k: s[k] for k in instacard.CARD_DEFAULTS} | (post.get("opts") or {})
    photo = Image.open(photo_path(post["photo"])).convert("RGB")
    img = instacard.compose(photo, post["title"], post["lang"], post.get("ratio") or s["ratio"],
                            logo=_logo(), brand=s["brand"], opts=opts)
    CARDS.mkdir(parents=True, exist_ok=True)
    card_path(post["id"]).write_bytes(instacard.to_jpeg(img))
    post["rev"] = post.get("rev", 0) + 1  # 화면 이미지 캐시 무효화용


def full_caption(post):
    tags = " ".join(f"#{t}" for t in post.get("hashtags") or [])
    return post["caption"] + (f"\n\n{tags}" if tags else "")


def _jpeg_b64(pid, side=768):
    img = Image.open(photo_path(pid)).convert("RGB")
    img.thumbnail((side, side), Image.LANCZOS)  # Claude에 보내는 사진은 작게 → 비용 절약
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return base64.standard_b64encode(out.getvalue()).decode()


def generate(photo_ids, langs=None, instruction=""):
    """선택한 사진 × 언어마다 게시물(제목·본문·카드)을 만든다. 같은 사진·언어 게시물이 있으면 새로 고친다."""
    s = settings()
    langs = [l for l in (langs or s["langs"]) if l in instacard.LANGS] or ["ko"]
    by_id = {p["id"]: p for p in photos()}
    ids = [i for i in photo_ids if i in by_id]
    if not ids:
        raise llm.UserError("게시물을 만들 사진을 먼저 선택하세요.")
    print(f"사진 {len(ids)}장 × 언어 {len(langs)}개 = 게시물 {len(ids) * len(langs)}개를 만듭니다.")
    written = {}
    if llm.available():
        batches = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
        print(f"Claude가 사진을 보고 언어별 제목·본문을 쓰는 중… ({len(batches)}묶음 동시 진행)")

        def write(batch):
            jobctl.check()
            items = [{"jpeg_b64": _jpeg_b64(i), "note": by_id[i].get("note", "")} for i in batch]
            res = llm.write_insta_posts(items, langs, topic=s["topic"], style=s["style"], hashtags=s["hashtags"],
                                        instruction=instruction)
            return {batch[k]: v for k, v in res.items()}

        failed = []
        with ThreadPoolExecutor(max_workers=min(4, len(batches))) as ex:
            futures = [(b, ex.submit(write, b)) for b in batches]
            for b, fut in futures:
                try:
                    written.update(fut.result())
                    print(f"  글쓰기 {len(written)}/{len(ids)}장 완료")
                except jobctl.Cancelled:
                    raise
                except Exception as e:  # 한 묶음이 실패해도 나머지 묶음 결과는 살린다
                    failed += b
                    print(f"  ! 사진 {len(b)}장 묶음 글쓰기 실패: {e}")
        if failed and not written:
            raise llm.UserError("Claude 글쓰기가 모두 실패했습니다. 위 오류를 확인하고 다시 시도해 주세요.")
        ids = [i for i in ids if i not in failed]
    else:
        print("※ Claude API 키가 없어 제목·본문은 비워 둡니다. 게시물 탭에서 직접 써 주세요. (카드 합성은 무료로 그대로 진행)")
    old = {(p["photo"], p["lang"]): p for p in posts()}
    made = 0
    for pid in ids:
        jobctl.check()
        if not photo_path(pid).exists():  # 만드는 도중에 지운 사진
            print(f"  ! {by_id[pid]['name']}: 사진이 삭제돼 건너뜁니다.")
            continue
        w = written.get(pid, {})
        for lang in langs:
            v = (w.get("versions") or {}).get(lang)
            prev = old.get((pid, lang))
            if llm.available() and not v:
                print(f"  ! {by_id[pid]['name']} ({instacard.LANGS[lang]}) 글을 받지 못해 건너뜁니다.")
                continue
            post = {**(prev or {}),
                    "id": prev["id"] if prev else _new_id("c"), "photo": pid, "lang": lang,
                    "title": v["title"] if v else (prev or {}).get("title", by_id[pid].get("note", "")[:40]),
                    "caption": v["caption"] if v else (prev or {}).get("caption", ""),
                    "hashtags": v["hashtags"] if v else (prev or {}).get("hashtags", []),
                    "risk": w.get("reuse_risk", (prev or {}).get("risk", "none")),
                    "risk_reason": w.get("risk_reason", (prev or {}).get("risk_reason", "")),
                    "ok_reuse": (prev or {}).get("ok_reuse", False),
                    "ratio": s["ratio"], "created": (prev or {}).get("created", time.time()),
                    "gen": (prev or {}).get("gen", 0) + 1}  # 글이 새로 만들어진 횟수 (화면의 묵은 수정 정리용)
            render_card(post, s)
            _save_post(post)
            made += 1
        if w.get("reuse_risk", "none") != "none":
            print(f"  ⚠ {by_id[pid]['name']}: {w['risk_reason'] or '남의 콘텐츠로 보입니다'} → 자동 업로드에서 제외")
    print(f"게시물 {made}개 완성 (카드 합성 비용 0원)")
    return made


def rewrite(post_id, instruction=""):
    """게시물 하나의 제목·본문만 Claude로 다시 쓴다 (예: '더 짧게', '더 궁금하게')."""
    post = get_post(post_id)
    if not post:
        raise llm.UserError("게시물을 찾을 수 없습니다.")
    if not llm.available():
        raise llm.UserError("다시 쓰기는 Claude API 키가 필요합니다.")
    s = settings()
    ph = next((p for p in photos() if p["id"] == post["photo"]), {})
    extra = f"Previous title was: {post['title']}. Write a clearly different one. " + instruction
    res = llm.write_insta_posts([{"jpeg_b64": _jpeg_b64(post["photo"]), "note": ph.get("note", "")}], [post["lang"]],
                                topic=s["topic"], style=s["style"], hashtags=s["hashtags"], instruction=extra)
    v = (res.get(0, {}).get("versions") or {}).get(post["lang"])
    if not v:
        raise llm.UserError("Claude가 글을 돌려주지 않았습니다. 다시 시도해 주세요.")
    with LOCK:  # Claude를 기다리는 동안 바뀐 다른 설정(글자 크기·확인 체크 등)은 살린다
        post = get_post(post_id)
        if not post:
            raise llm.UserError("그사이 게시물이 삭제됐습니다.")
        post.update(title=v["title"], caption=v["caption"], hashtags=v["hashtags"], gen=post.get("gen", 0) + 1)
        render_card(post, s)
        _save_post(post)
    return post


def update_post(post_id, changes):
    """화면에서 고친 제목·본문·해시태그·글자 크기 등을 저장하고 카드를 다시 합성."""
    with LOCK:
        post = get_post(post_id)
        if not post:
            raise llm.UserError("게시물을 찾을 수 없습니다.")
        for k in ("title", "caption"):
            if k in changes:
                post[k] = str(changes[k])
        if "hashtags" in changes:
            tags = changes["hashtags"]
            if isinstance(tags, str):
                tags = re.split(r"[\s,]+", tags)
            post["hashtags"] = [t.strip().lstrip("#") for t in tags if t.strip("# ")][:MAX_HASHTAGS]
        if "ok_reuse" in changes:
            post["ok_reuse"] = bool(changes["ok_reuse"])
        if "opts" in changes:
            opts = {}
            for k, v in (changes["opts"] or {}).items():
                if k in ("font_scale", "gradient", "photo_ratio"):
                    opts[k] = float(v)
                elif k in ("upper", "enhance"):
                    opts[k] = bool(v)
                elif k == "logo_pos" and v in ("left", "center", "right"):
                    opts[k] = v
            post["opts"] = opts
        if "ratio" in changes and changes["ratio"] in instacard.RATIOS:
            post["ratio"] = changes["ratio"]
        render_card(post)
        _save_post(post)
        return post


def rerender_all():
    """로고·브랜드·글자 설정을 바꾼 뒤 모든 카드를 다시 합성 (무료, 몇 초)."""
    s = settings()
    ids = [p["id"] for p in posts()]
    for i, pid in enumerate(ids):
        jobctl.check()
        post = get_post(pid)  # 하나씩 최신본을 읽는다 (그사이 고치거나 지운 게시물 존중)
        if post and photo_path(post["photo"]).exists():
            rev = post.get("rev", 0)
            render_card(post, s)
            _save_post(post, if_rev=rev)
        print(f"\r카드 다시 합성 {i + 1}/{len(ids)}", end="", flush=True)
    print()


def delete_post(post_id):
    with LOCK:
        _save("posts.json", [p for p in posts() if p["id"] != post_id])
        card_path(post_id).unlink(missing_ok=True)


def can_autopost(post):
    """남의 콘텐츠로 보이는 사진은 사용자가 '내 사진 맞음'을 확인해야 자동 업로드한다."""
    return post.get("risk", "none") == "none" or post.get("ok_reuse")


def export(post_ids, out_root):
    """선택한 카드 + 본문(txt)을 저장 폴더/인스타_게시물/날짜/언어/ 에 복사. 직접 올릴 때 쓴다."""
    folder = Path(out_root) / "인스타_게시물" / time.strftime("%Y-%m-%d")
    n = 0
    for post in posts():
        if post["id"] not in post_ids or not card_path(post["id"]).exists():
            continue
        d = folder / instacard.LANGS[post["lang"]]
        d.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r'[\\/:*?"<>|\[\]\n]', "", post["title"])[:40].strip() or post["id"]
        shutil.copyfile(card_path(post["id"]), d / f"{stem}_{post['id'][-4:]}.jpg")
        (d / f"{stem}_{post['id'][-4:]}.txt").write_text(full_caption(post), encoding="utf-8-sig")
        n += 1
    return folder, n


def mark_published(photo_id):
    """올린 사진의 지문을 기록 → 다음에 같은 사진을 넣으면 '이미 올린 사진'으로 표시."""
    ph = next((p for p in photos() if p["id"] == photo_id), None)
    if not ph:
        return
    with LOCK:
        hist = _load("history.json", {})
        hashes = hist.setdefault("hashes", [])
        if ph["hash"] not in hashes:
            hashes.append(ph["hash"])
        _save("history.json", hist)
