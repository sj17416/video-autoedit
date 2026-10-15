"""검색 유입이 늘고 체류시간이 긴 글 고르기."""
import math
import re
import statistics
from datetime import date
from urllib.parse import unquote, urlparse

MIN_VIEWS = 10        # 체류시간 평균을 믿을 만한 최소 조회수
MIN_GROWTH = 1.2      # 최근 절반 기간 클릭이 이전 절반보다 20% 이상
MAX_GROWTH = 5.0      # 클릭 몇 개짜리 글이 점수를 독차지하지 않게
GAP_POSITION = 8      # 이보다 순위가 낮으면 아직 제대로 못 잡은 검색어
GAP_CTR = 0.02


def norm_path(url_or_path):
    """GSC 전체 주소와 GA4 경로를 같은 키로 맞춘다. 티스토리 모바일(/m/)과 끝 슬래시도 정리."""
    path = unquote(urlparse(url_or_path).path or "/")
    path = re.sub(r"^/m(?=/)", "", path)
    return path.rstrip("/") or "/"


def clean_title(title):
    """티스토리가 붙이는 ' :: 블로그이름' 꼬리를 뗀다."""
    return re.split(r"\s+::\s+", title)[0].strip()


def merge_ga(ga_rows):
    pages = {}
    for r in ga_rows:
        p = pages.setdefault(norm_path(r["path"]), {"views": 0, "engagement": 0.0, "titles": {}})
        p["views"] += r["views"]
        p["engagement"] += r["engagement"]
        p["titles"][r["title"]] = p["titles"].get(r["title"], 0) + r["views"]
    for p in pages.values():
        p["title"] = clean_title(max(p["titles"], key=p["titles"].get)) if p["titles"] else ""
        p["sec_per_view"] = p["engagement"] / p["views"] if p["views"] else 0.0
    return pages


def weekly_clicks(daily_rows, end, weeks):
    """{경로: [가장 최근 주, 그 전 주, ...]}"""
    out = {}
    for r in daily_rows:
        idx = (end - date.fromisoformat(r["date"])).days // 7
        if 0 <= idx < weeks:
            out.setdefault(norm_path(r["page"]), [0] * weeks)[idx] += r["clicks"]
    return out


def rank_pages(daily_rows, ga_rows, end, weeks=4, min_clicks=5, top=3):
    """반환: (뜨는 글 목록, 전체 글 표, 블로그 체류시간 중앙값)."""
    ga = merge_ga(ga_rows)
    trusted = [p["sec_per_view"] for p in ga.values() if p["views"] >= MIN_VIEWS]
    median = statistics.median(trusted) if trusted else 0.0
    half = weeks // 2
    table = []
    for path, w in weekly_clicks(daily_rows, end, weeks).items():
        g = ga.get(path, {})
        recent, older = sum(w[:half]), sum(w[half:])
        growth = (recent + 1) / (older + 1)
        sec = g.get("sec_per_view", 0.0) if g.get("views", 0) >= MIN_VIEWS else 0.0
        ratio = sec / median if median else 0.0
        table.append({
            "path": path, "title": g.get("title", ""), "weekly": w[::-1], "recent": recent, "older": older,
            "growth": growth, "views": g.get("views", 0), "sec_per_view": sec, "engage_ratio": ratio,
            "score": min(growth, MAX_GROWTH) * ratio * math.log1p(recent),
            "rising": recent >= min_clicks and growth >= MIN_GROWTH and ratio >= 1.0,
        })
    table.sort(key=lambda r: r["score"], reverse=True)
    winners = [r for r in table if r["rising"]][:top]
    return winners, table, median


def page_queries(query_rows, path, limit=20):
    """한 글로 들어온 검색어. gap=True면 수요는 있는데 순위·클릭률이 낮은 검색어(새 글/보강 후보)."""
    rows = [r for r in query_rows if norm_path(r["page"]) == path]
    rows.sort(key=lambda r: r["impressions"], reverse=True)
    return [{"query": r["query"], "clicks": r["clicks"], "impressions": r["impressions"],
             "position": round(r["position"], 1), "ctr": round(r["ctr"], 3),
             "gap": r["position"] > GAP_POSITION or r["ctr"] < GAP_CTR}
            for r in rows[:limit]]
