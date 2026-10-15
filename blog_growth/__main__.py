"""블로그 주간 분석: 뜨는 글 찾기 → 보강/새 글 제안 → 초안 → 보고서.

    python -m blog_growth [--blog 이름] [--dry-run] [--open]
"""
import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autoedit import env, llm  # noqa: E402
from autoedit.llm import UserError  # noqa: E402
from blog_growth import analyze, google_data, report, structure, writer  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
GSC_LAG_DAYS = 3


def load_config():
    path = HERE / "config.json"
    if not path.exists():
        raise UserError("blog_growth/config.json이 없습니다. config.example.json을 복사해서 이름을 바꾸고 내용을 채워 주세요.")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for b in cfg["blogs"]:
        for k in ("name", "blog_url", "search_console_site", "ga4_property_id"):
            if not str(b.get(k, "")).strip():
                raise UserError(f"config.json의 블로그 '{b.get('name', '?')}'에 {k} 값이 비어 있습니다.")
    return cfg


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "blog"


def run_blog(blog, sess, email, args):
    end = date.today() - timedelta(days=GSC_LAG_DAYS)
    start = end - timedelta(days=7 * args.weeks - 1)
    site, prop = blog["search_console_site"], str(blog["ga4_property_id"]).strip()
    print(f"\n[{blog['name']}] {start} ~ {end} 데이터 가져오는 중...")
    daily = google_data.search_console(sess, email, site, start, end, ["page", "date"])
    queries = google_data.search_console(sess, email, site, start, end, ["page", "query"])
    ga = google_data.ga4_pages(sess, email, prop, start, end)
    print(f"  검색 데이터 {len(daily)}행, 검색어 {len(queries)}행, 애널리틱스 {len(ga)}개 주소")

    winners, table, median = analyze.rank_pages(daily, ga, end, weeks=args.weeks, top=args.top)
    for w in winners:
        w["queries"] = analyze.page_queries(queries, w["path"])
    print(f"  뜨는 글 {len(winners)}개" + "".join(f"\n   - {w['title'] or w['path']} (주별 클릭 {w['weekly']})" for w in winners))

    folder = OUT / safe_name(blog["name"])
    state_file = folder / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"proposed": []}
    plan, drafts, note = None, {}, ""
    if not winners:
        note = ("이번 주에는 기준(검색 클릭 증가 + 체류시간 중앙값 이상)을 넘은 글이 없습니다. "
                "글이 30개 미만이거나 블로그를 연 지 2~3개월이 안 됐다면 정상입니다. 계획한 주제대로 계속 써 주세요.")
    elif args.dry_run:
        note = "--dry-run: Claude 기획/초안은 건너뛰었습니다."
    else:
        titles = sorted({analyze.clean_title(r["title"]) for r in ga if r["title"]})
        proposed = [p["title"] for p in state["proposed"]]
        print("  Claude가 보강할 내용과 새 주제를 고르는 중...")
        plan = writer.plan(blog, winners, titles, proposed)
        by_path = {w["path"]: w for w in winners}
        for t in plan["topics"][:blog.get("drafts_per_week", 2)]:
            winner = by_path.get(t["source_path"], winners[0])
            print(f"  초안 작성 중: {t['title']}")
            shape = structure.outline(blog["blog_url"].rstrip("/") + winner["path"])
            drafts[t["title"]] = writer.draft(blog, t, winner, shape)
        state["proposed"] += [{"date": end.isoformat(), "title": t["title"], "target_query": t["target_query"]}
                              for t in plan["topics"]]

    day_dir = folder / end.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    for i, (title, html) in enumerate(drafts.items(), 1):
        (day_dir / f"초안{i}_{safe_name(title)[:40]}.html").write_text(html, encoding="utf-8")
    page = day_dir / "보고서.html"
    page.write_text(report.build(blog, start, end, winners, table, median, plan, drafts, note), encoding="utf-8")
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  보고서: {page}")
    return page


def main():
    ap = argparse.ArgumentParser(description="블로그 주간 분석")
    ap.add_argument("--blog", help="config.json의 블로그 이름 하나만 실행")
    ap.add_argument("--weeks", type=int, default=4, help="비교 기간(주), 짝수")
    ap.add_argument("--top", type=int, default=3, help="뜨는 글 최대 개수")
    ap.add_argument("--dry-run", action="store_true", help="Claude 호출 없이 데이터 분석만")
    ap.add_argument("--open", action="store_true", help="끝나면 보고서 열기")
    args = ap.parse_args()
    env.load_env(ROOT)
    try:
        cfg = load_config()
        blogs = [b for b in cfg["blogs"] if not args.blog or b["name"] == args.blog]
        if not blogs:
            raise UserError(f"config.json에 '{args.blog}' 블로그가 없습니다.")
        if not args.dry_run and not llm.available():
            raise UserError(".env에 ANTHROPIC_API_KEY가 없습니다. 영상 편집기 설정에서 넣은 키가 그대로 쓰입니다.")
        sess, email = google_data.session(HERE / cfg.get("google_key_file", "google_key.json"))
        pages = [run_blog(b, sess, email, args) for b in blogs]
    except UserError as e:
        print(f"\n오류: {e}")
        return 1
    if args.open and hasattr(os, "startfile"):
        for p in pages:
            os.startfile(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
