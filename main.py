"""유튜브 영상 자동 편집기 (명령줄 버전). 웹앱은 app.py.

  python main.py 영상.mp4

완성본은 이집트 폴더(웹앱에서 바꾼 저장 위치)에 저장되고, 중간 결과는 projects/ 에 남아
웹앱에서 이어서 수정할 수 있다. 다시 실행하면 끝난 단계는 건너뛴다.
"""
import os
import sys
import warnings

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
warnings.filterwarnings("ignore", module="huggingface_hub")

try:  # ctranslate2(Whisper)가 VC++ 런타임 DLL을 먼저 로드해야 OCR/OpenCV도 정상 동작한다
    import ctranslate2  # noqa: F401
except ImportError as e:
    sys.exit(f"Whisper 엔진 로드 실패: {e}\n'Microsoft Visual C++ 재배포 가능 패키지(x64)'를 설치한 뒤 다시 실행하세요.")

import argparse
import shutil
from pathlib import Path


from autoedit import env, llm
from autoedit.pipeline import ROOT, STEPS, load_project, output_dir, project_for_file


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    env.load_env(ROOT)
    ap = argparse.ArgumentParser(description="유튜브 영상 자동 편집기")
    ap.add_argument("videos", nargs="+", help="편집할 영상 파일 (여러 개 가능)")
    ap.add_argument("-o", "--output", help="출력 파일 경로 (영상 1개일 때만)")
    ap.add_argument("--whisper", help="Whisper 모델: turbo(기본, 빠르고 정확) / medium / large-v3 / small")
    ap.add_argument("--speed", choices=["fast", "balanced", "best"],
                    help="fast: 1080p·30fps(기본) / balanced: 1080p·원본fps / best: 원본 해상도 그대로")
    ap.add_argument("--max-pause", type=float, help="이 길이(초) 이하의 쉼은 자르지 않음 (기본 0.45)")
    ap.add_argument("--keep-pause", type=float, help="긴 쉼을 자를 때 남겨둘 호흡 길이(초) (기본 0.25)")
    ap.add_argument("--scene-every", type=float, help="설명 이미지 최대 빈도: N초에 1개 (기본 20)")
    ap.add_argument("--scan-fps", type=float, help="개인정보 OCR 스캔 빈도 (기본 1)")
    ap.add_argument("--no-scenes", action="store_true", help="설명 이미지 넣지 않기")
    ap.add_argument("--no-mosaic", action="store_true", help="화면 개인정보 모자이크 하지 않기")
    ap.add_argument("--no-beep", action="store_true", help="말로 나온 개인정보 삐 처리 하지 않기")
    ap.add_argument("--no-subtitles", action="store_true", help="영상에 자막 넣지 않기 (SRT 파일은 만듦)")
    ap.add_argument("--vocab", help="자주 나오는 용어 (쉼표로 구분, 예: \"복지로, 중위소득\")")
    ap.add_argument("--redo", nargs="*", default=[], choices=STEPS + ["all"],
                    help="저장된 중간 결과를 버리고 다시 할 단계")
    args = ap.parse_args()
    if args.output and len(args.videos) > 1:
        ap.error("-o 는 영상이 1개일 때만 쓸 수 있습니다.")
    if not llm.available():
        print("※ ANTHROPIC_API_KEY가 없어 설명 이미지/음성 개인정보 탐지는 건너뜁니다. (.env 파일 참고)")

    changes = {k: v for k, v in {"whisper": args.whisper, "max_pause": args.max_pause, "keep_pause": args.keep_pause,
                                 "scene_every": args.scene_every, "scan_fps": args.scan_fps, "vocab": args.vocab}.items() if v is not None}
    changes.update({k: False for k, flag in (("cards", args.no_scenes), ("objects", args.no_scenes),
                                             ("mosaic", args.no_mosaic), ("beep", args.no_beep),
                                             ("subtitles", args.no_subtitles)) if flag})
    if args.speed:
        changes["speed"] = args.speed
    print(f"저장 위치: {output_dir()}")
    for v in args.videos:
        src = Path(v).resolve()
        if not src.exists():
            print(f"파일이 없습니다: {src}")
            continue
        proj = load_project(project_for_file(src))  # 웹앱 프로젝트 목록에도 나타난다
        proj.settings.update(changes)  # 명령줄 옵션은 이번 실행에만 적용
        for step in (STEPS if "all" in args.redo else args.redo):
            proj.reset(step)
        try:
            proj.run_all()
        except llm.UserError as e:
            sys.exit(f"\n오류: {e}")
        if args.output:
            shutil.move(proj.out, args.output)
            shutil.move(proj.out.with_suffix(".srt"), Path(args.output).with_suffix(".srt"))
        print(f"\n완료! (웹앱에서 이어서 수정할 수 있습니다: 웹앱실행.bat)")


if __name__ == "__main__":
    main()
