"""레시피 쇼츠 레이더 (명령줄). 최근 조회수가 터진 레시피 쇼츠를 모아 순위표를 만든다.

  python radar.py [--days 7] [--top 20] [--out 폴더]

YOUTUBE_API_KEY 환경변수(또는 .env)가 필요하다. 결과: 폴더/latest.md, 폴더/<시각>.json, 폴더/history.json
"""
import argparse
import sys
from pathlib import Path

from autoedit import env
from autoedit.pipeline import ROOT
from datetime import datetime

from autoedit.radar import QUERIES, RadarError, collect


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="터진 레시피 쇼츠 모으기 (유튜브 API)")
    ap.add_argument("--days", type=int, default=7, help="최근 며칠 안에 올라온 영상만 (기본 7)")
    ap.add_argument("--per-query", type=int, default=25, help="검색어마다 가져올 개수 (최대 50)")
    ap.add_argument("--top", type=int, default=20, help="순위표에 남길 개수")
    ap.add_argument("--out", default=str(ROOT / "radar"), help="결과 저장 폴더")
    ap.add_argument("--rotate", type=int, default=0,
                    help="검색어를 N개씩 돌아가며 쓰기 (매시간 돌릴 때 하루 한도 10,000점을 넘지 않게. 0 = 전부)")
    args = ap.parse_args()
    queries = QUERIES
    if args.rotate:
        start = (datetime.now().hour * args.rotate) % len(QUERIES)
        queries = [QUERIES[(start + i) % len(QUERIES)] for i in range(args.rotate)]
    try:
        rows = collect(Path(args.out), queries=queries, days=args.days, per_query=args.per_query, top=args.top)
    except RadarError as e:
        sys.exit(str(e))
    print((Path(args.out) / "latest.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
