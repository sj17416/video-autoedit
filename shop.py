"""쇼핑 쇼츠 만들기 (명령줄 버전). 웹앱의 '쇼핑 쇼츠' 칸과 같은 일을 한다.

  python shop.py 폴더 [--bgm 음악.mp3] [--voice 여성|남성|없음] [--keyword 쫀득]

폴더에는 직접 찍은 클립들과 대본.txt(한 줄 = 한 컷)를 넣는다. 형식은 autoedit/shopping.py 맨 위 설명 참고.
완성본은 저장 위치(기본: 이집트 폴더)에 '폴더이름_쇼핑.mp4'로 저장된다.
"""
import argparse
import sys
from pathlib import Path

from autoedit import env, jobctl, llm
from autoedit.pipeline import ROOT, output_dir
from autoedit.shopping import VOICES, list_clips, make_short


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="쇼핑 쇼츠 만들기: 클립 + 대본 → 세로 쇼츠")
    ap.add_argument("folder", help="클립들과 대본.txt가 있는 폴더")
    ap.add_argument("--script", help="대본 파일 (기본: 폴더 안의 대본.txt)")
    ap.add_argument("--bgm", help="배경음악 파일 (선택)")
    ap.add_argument("--voice", default="여성", choices=[*VOICES, "없음"], help="AI 목소리 (없음 = 클립 소리만)")
    ap.add_argument("--rate", default="+10%", help="말 빠르기 (예: +10%%, -5%%)")
    ap.add_argument("--keyword", help="노란색으로 강조할 키워드 (기본: 대본의 '키워드:' 줄)")
    ap.add_argument("-o", "--output", help="출력 파일 경로")
    args = ap.parse_args()

    folder = Path(args.folder).resolve()
    script = Path(args.script) if args.script else folder / "대본.txt"
    if not script.exists():
        sys.exit(f"대본 파일이 없습니다: {script}")
    out = Path(args.output) if args.output else output_dir() / f"{folder.name}_쇼핑.mp4"
    try:
        make_short(list_clips(folder), script.read_text(encoding="utf-8-sig"), out, folder / "_쇼핑작업",
                   keyword=args.keyword, voice=args.voice, rate=args.rate, bgm=args.bgm)
    except (llm.UserError, jobctl.Cancelled) as e:
        sys.exit(f"\n오류: {e}")


if __name__ == "__main__":
    main()
