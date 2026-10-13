"""AI 사진 → 움직이는 AI 영상 (구글 Veo, 이미지→영상).

  python tools/veo_scenes.py 움직임.json 작업폴더 [--model veo-3.1-fast-generate-preview] [--seconds 4]

움직임.json 은 {"s01_hook": "움직임 설명(영어)", ...}. 작업폴더에 tools/ai_scenes.py 가 만든 s01_hook.png 가 있어야 한다.
결과는 같은 이름의 s01_hook.mp4 로 덮어쓴다(정지 사진 클립 → 진짜 움직이는 클립). 실패한 장면은 정지 클립을 그대로 둔다.
환경변수 GEMINI_API_KEY 가 필요하다(유료: Veo 는 무료 등급이 없다). 요금은 초 단위라 꼭 필요한 장면만 고른다.
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autoedit import env  # noqa: E402
from autoedit.pipeline import ROOT  # noqa: E402

STYLE = ("Realistic handheld smartphone cooking video, natural subtle camera movement, only hands visible, "
         "no faces, no text, no captions, no watermark. ")


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="AI 사진 → 움직이는 영상 (Veo)")
    ap.add_argument("motions", help="장면별 움직임 설명 JSON")
    ap.add_argument("folder", help="장면 PNG 가 있는 작업 폴더")
    ap.add_argument("--model", default=os.environ.get("VEO_MODEL", "veo-3.1-fast-generate-preview"))
    ap.add_argument("--seconds", type=int, default=4, choices=[4, 6, 8])
    ap.add_argument("--resolution", default="720p", choices=["720p", "1080p"])
    args = ap.parse_args()
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY 가 없습니다. 구글 AI 스튜디오에서 키를 받아 프로젝트 설정의 환경 변수에 넣어 주세요.")

    from google import genai
    from google.genai import types
    client = genai.Client()
    folder = Path(args.folder)
    motions = json.loads(Path(args.motions).read_text(encoding="utf-8"))

    def make(item):
        name, motion = item
        png = folder / f"{name}.png"
        if not png.exists():
            return name, "사진 없음"
        op = client.models.generate_videos(
            model=args.model, prompt=STYLE + motion,
            image=types.Image(image_bytes=png.read_bytes(), mime_type="image/png"),
            config=types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=args.seconds,
                                              resolution=args.resolution, number_of_videos=1,
                                              negative_prompt="text, captions, watermark, logo, face, distorted hands"))
        while not op.done:
            time.sleep(10)
            op = client.operations.get(op)
        if op.error or not op.response or not op.response.generated_videos:
            return name, f"실패: {op.error}"
        video = op.response.generated_videos[0].video
        client.files.download(file=video)
        video.save(str(folder / f"{name}.mp4"))
        return name, "완성"

    try:
        with cf.ThreadPoolExecutor(4) as ex:
            for name, status in ex.map(make, motions.items()):
                print(f"{name}: {status}", flush=True)
    except Exception as e:  # 모델 이름이 틀렸을 때 쓸 수 있는 Veo 모델을 알려 준다
        names = [m.name for m in client.models.list() if "veo" in m.name]
        sys.exit(f"Veo 오류: {e}\n쓸 수 있는 Veo 모델: {', '.join(names) or '없음'}")


if __name__ == "__main__":
    main()
