"""AI 조리 장면 만들기: 장면 설명(JSON) → OpenAI 이미지(gpt-image-2) → 쇼핑 쇼츠용 4초 세로 클립(mp4).

  python tools/ai_scenes.py scenes.json 출력폴더 [--model gpt-image-2] [--quality medium]

scenes.json 은 {"s01_hook": "장면 설명(영어)", ...}. 출력폴더에 s01_hook.png 와 s01_hook.mp4 가 생긴다.
그 폴더에 대본.txt 를 두고 shop.py 로 렌더하면 된다(카메라 움직임·자막·목소리는 shop.py 가 입힌다).
Sora(동영상) API 는 2026-09-24 에 종료돼서 정지 사진 + 카메라 움직임으로 만든다.
"""
import argparse
import base64
import concurrent.futures as cf
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ctranslate2  # noqa: F401,E402  (Windows: 런타임 DLL 을 먼저 올린다 — CLAUDE.md 참고)
from autoedit import env, media  # noqa: E402
from autoedit.pipeline import ROOT  # noqa: E402

STYLE = ("Real candid smartphone photo (iPhone main camera, vertical) taken in an ordinary Korean home kitchen, not a studio. "
         "Natural indoor light, slight imperfections: a few crumbs, small splashes, real steam, slightly uneven cuts, "
         "natural food texture and true-to-life colors, no over-saturation, no CGI look. Only real-looking hands visible "
         "(natural skin, short nails), no faces, no text, no logos, no brand labels, no watermark. ")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="AI 조리 장면 → 세로 클립")
    ap.add_argument("scenes", help="장면 설명 JSON")
    ap.add_argument("out", help="출력 폴더")
    ap.add_argument("--model", default="gpt-image-2")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high"])
    ap.add_argument("--seconds", type=float, default=4.0, help="클립 길이 (대사보다 길면 됨)")
    args = ap.parse_args()

    from openai import OpenAI
    client = OpenAI(max_retries=6)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scenes = json.loads(Path(args.scenes).read_text(encoding="utf-8"))

    def make(item):
        name, prompt = item
        png = out / f"{name}.png"
        if not png.exists():
            r = client.images.generate(model=args.model, prompt=STYLE + prompt, size="1024x1536", quality=args.quality)
            png.write_bytes(base64.b64decode(r.data[0].b64_json))
        media.run(["-loop", "1", "-i", str(png), "-t", f"{args.seconds}", "-r", "30",
                   "-vf", "scale=-2:1920,crop=1080:1920,format=yuv420p",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out / f"{name}.mp4")])
        return name

    with cf.ThreadPoolExecutor(10) as ex:
        for name in ex.map(make, scenes.items()):
            print("장면 완성:", name, flush=True)


if __name__ == "__main__":
    main()
