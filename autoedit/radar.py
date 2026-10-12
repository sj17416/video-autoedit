"""레시피 쇼츠 레이더: 유튜브 Data API v3로 최근 조회수가 터진 레시피 쇼츠를 모은다.

- 키는 환경변수 YOUTUBE_API_KEY (env.load_env 로 .env 에서도 읽음).
- 한도: 검색 1번 = 100점, 영상·채널 정보 50개 = 1점. 하루 10,000점 → 4시간마다 돌려도 넉넉하게 검색어 수를 잡는다.
- 모을 때마다 영상별 조회수를 기록(history)해 두고, 다음에 돌릴 때 '하루 증가량'을 계산한다.
- 점수: 조회수, 구독자 대비 배수, 하루 증가량을 섞는다. 크리에이티브 커먼즈(CC) 영상은 표시해 둔다.
- 남의 영상은 내려받지 않는다. 제목·숫자·링크만 저장한다.
"""
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://www.googleapis.com/youtube/v3/"

# (검색어, 지역, 언어) — 한국 먼저, 해외(미국·일본)는 한국에서 아직 안 터진 아이디어를 찾는 용도
QUERIES = [
    ("레시피", "KR", "ko"),
    ("간단 요리", "KR", "ko"),
    ("자취 요리", "KR", "ko"),
    ("다이어트 레시피", "KR", "ko"),
    ("에어프라이어 요리", "KR", "ko"),
    ("easy recipe", "US", "en"),
    ("レシピ", "JP", "ja"),
]
MAX_SECONDS = 90          # 이보다 긴 영상은 쇼츠로 보지 않는다
# 지역 검색에 섞여 들어오는 다른 나라 영상을 거른다 (제목 글자로 판단)
SCRIPT_OK = {
    "KR": lambda t: re.search(r"[가-힣]", t),
    "JP": lambda t: re.search(r"[ぁ-んァ-ン]", t),
    "US": lambda t: sum(c.isascii() for c in t) / max(len(t), 1) > 0.9 and not re.search(r"[가-힣ぁ-んァ-ン]", t),
}
# 영어 제목인데 인도·동남아 쪽 영상이 많이 섞여서, 오디오 언어가 적혀 있으면 그것도 본다
LANG_OK = {"KR": ("ko",), "JP": ("ja",), "US": ("en",)}


class RadarError(Exception):
    pass


def _key():
    k = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not k:
        raise RadarError("YOUTUBE_API_KEY 가 없습니다. 프로젝트 설정의 환경 변수에 넣어 주세요.")
    return k


def _get(endpoint, **params):
    params["key"] = _key()
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read().decode("utf-8"))["error"]["message"]
        except Exception:
            reason = str(e.code)
        raise RadarError(f"유튜브 API 오류 ({endpoint}): {reason}") from None


def _seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _chunks(items, n=50):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def search(queries=QUERIES, days=7, per_query=25):
    """검색어마다 최근 days일 안에 올라온 짧은 영상을 조회수 순으로 찾는다. {id: (검색어, 지역)}"""
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}
    for q, region, lang in queries:
        res = _get("search", part="id", q=q, type="video", videoDuration="short", order="viewCount",
                   regionCode=region, relevanceLanguage=lang, publishedAfter=after, maxResults=per_query)
        for it in res.get("items", []):
            found.setdefault(it["id"]["videoId"], (q, region))
    return found


def details(ids):
    """영상 정보 + 채널 구독자 수."""
    videos = []
    for part in _chunks(list(ids)):
        res = _get("videos", part="snippet,statistics,contentDetails,status", id=",".join(part))
        videos += res.get("items", [])
    subs = {}
    for part in _chunks(sorted({v["snippet"]["channelId"] for v in videos})):
        res = _get("channels", part="statistics", id=",".join(part))
        for c in res.get("items", []):
            st = c.get("statistics", {})
            subs[c["id"]] = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
    return videos, subs


def collect(store_dir, queries=QUERIES, days=7, per_query=25, top=20):
    """모아서 store_dir 에 기록하고, 점수 순 상위 목록(dict 리스트)을 돌려준다."""
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    hist_path = store_dir / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    now = datetime.now(timezone.utc)

    found = search(queries, days, per_query)
    videos, subs = details(found)
    rows = []
    for v in videos:
        sec = _seconds(v["contentDetails"].get("duration"))
        if not sec or sec > MAX_SECONDS:
            continue
        sn, st = v["snippet"], v.get("statistics", {})
        q, region = found.get(v["id"], ("", ""))
        lang = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").split("-")[0]
        if region in SCRIPT_OK and not SCRIPT_OK[region](sn["title"]):
            continue
        if lang and region in LANG_OK and lang not in LANG_OK[region]:
            continue
        views = int(st.get("viewCount", 0))
        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        age_days = max((now - published).total_seconds() / 86400, 0.25)
        # 하루 증가량: 지난 기록이 있으면 그 사이 증가분, 없으면 올린 뒤 평균
        prev = history.get(v["id"], [])
        growth = views / age_days
        if prev:
            t0, v0 = prev[-1]
            hours = (now - datetime.fromisoformat(t0)).total_seconds() / 3600
            if hours >= 1:
                growth = (views - v0) * 24 / hours
        sub = subs.get(sn["channelId"])
        ratio = views / sub if sub else None
        score = math.log10(views + 1) + math.log10(growth + 1) + (math.log10(ratio + 1) if ratio else 0)
        rows.append({
            "id": v["id"], "url": f"https://youtube.com/shorts/{v['id']}", "title": sn["title"],
            "channel": sn["channelTitle"], "published": sn["publishedAt"][:10], "seconds": sec,
            "views": views, "likes": int(st.get("likeCount", 0)), "comments": int(st.get("commentCount", 0)),
            "subscribers": sub, "views_per_sub": round(ratio, 1) if ratio else None,
            "daily_growth": int(growth), "cc": v.get("status", {}).get("license") == "creativeCommon",
            "query": q, "region": region, "score": round(score, 2),
        })
        history.setdefault(v["id"], []).append([now.isoformat(), views])
        history[v["id"]] = history[v["id"]][-12:]

    hist_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    rows.sort(key=lambda r: r["score"], reverse=True)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    (store_dir / f"{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (store_dir / "latest.md").write_text(report(rows[:top], stamp), encoding="utf-8")
    return rows[:top]


def report(rows, stamp=""):
    lines = [f"# 터진 레시피 쇼츠 {stamp}", "",
             "| # | 제목 | 채널 | 조회수 | 하루 증가 | 구독자 대비 | 길이 | 지역 | CC |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        ratio = f"{r['views_per_sub']}배" if r["views_per_sub"] else "-"
        title = r["title"].replace("|", "/")[:40]
        lines.append(f"| {i} | [{title}]({r['url']}) | {r['channel'][:16]} | {r['views']:,} | {r['daily_growth']:,} "
                     f"| {ratio} | {r['seconds']}초 | {r['region']} | {'CC' if r['cc'] else ''} |")
    return "\n".join(lines) + "\n"
