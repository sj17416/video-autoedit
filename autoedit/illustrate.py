"""설명 이미지 생성 (Claude가 그린 SVG → PNG).

- object: 투명 배경 스티커 (아이콘/로고 대용) → 영상 위에 오버레이
- card:   배경이 있는 작은 일러스트 → 화면 중앙 카드
style_refs/ 폴더의 이미지를 그림체 참고용으로 Claude에게 함께 보낸다.
scenes/<id>.png 를 직접 만든 그림(투명 PNG 가능)으로 바꿔 넣으면 그대로 쓴다.
"""
import base64
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import resvg_py

from . import imagegen, jobctl, llm

FONT_KW = dict(font_family="Malgun Gothic", sans_serif_family="Malgun Gothic", serif_family="Malgun Gothic")
REF_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def canvas_for(kind, video_size):
    if kind == "object":
        return 1024, 1024
    w, h = video_size
    return (1600, 900) if w >= h else (900, 1125)


def render_svg(svg, width, height):
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height, **FONT_KW))


def load_refs(ref_dir, limit=4, max_side=1024):
    """참고 이미지를 줄여서 (media_type, base64) 목록으로."""
    refs = []
    paths = sorted(p for p in Path(ref_dir).glob("*") if p.suffix.lower() in REF_EXTS) if Path(ref_dir).exists() else []
    for p in paths[:limit]:
        img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        s = min(1.0, max_side / max(img.shape[:2]))
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            refs.append(("image/jpeg", base64.b64encode(buf.tobytes()).decode()))
    return refs


def resolve_engine(engine):
    """auto: OpenAI 키가 있으면 OpenAI(그림이 더 풍부), 없으면 Claude(SVG)."""
    if engine == "auto":
        return "openai" if imagegen.available() else "claude"
    return engine


def draw_one(scene, scenes_dir, video_size, refs, engine="claude"):
    jobctl.check()
    scenes_dir.mkdir(parents=True, exist_ok=True)
    png_path = scenes_dir / f"{scene['id']}.png"
    if engine == "openai":
        png_path.write_bytes(imagegen.generate(scene, refs))
        return png_path.name
    w, h = canvas_for(scene["kind"], video_size)
    error = None
    for _ in range(2):
        raw = llm.draw_svg(scene, w, h, scene.get("context", ""), refs, error)
        m = re.search(r"<svg[\s\S]*</svg>", raw)
        if not m:
            error = "no <svg> element in output"
            continue
        svg = m.group(0)
        (scenes_dir / f"{scene['id']}.svg").write_text(svg, encoding="utf-8")
        try:
            png_path.write_bytes(render_svg(svg, w, h))
            return png_path.name
        except Exception as e:  # 렌더 실패 시 오류를 알려주고 한 번 더 그리게 한다
            error = str(e)[:500]
    print(f"  ! 장면 {scene['id']} 그림 생성 실패: {error}")
    return None


def make_illustrations(scenes, scenes_dir, video_size, ref_dir, engine="auto", workers=6):
    """이미지가 없는 장면만 그린다 (여러 장을 동시에)."""
    todo = [s for s in scenes if s.get("enabled", True) and not (s.get("image") and (scenes_dir / s["image"]).exists())]
    if not todo:
        print("  그릴 장면 없음 (모두 완료)")
        return scenes
    refs = load_refs(ref_dir)
    engine = resolve_engine(engine)
    name = f"OpenAI {imagegen.MODEL}" if engine == "openai" else "Claude SVG"
    print(f"  {len(todo)}개 장면을 {name}로 그리는 중... (그림체 참고 이미지 {len(refs)}장)")
    with ThreadPoolExecutor(workers) as ex:
        futures = [(s, ex.submit(draw_one, s, scenes_dir, video_size, refs, engine)) for s in todo]
        for s, f in futures:
            s["image"] = f.result()
            kind = "오브젝트" if s["kind"] == "object" else "카드"
            print(f"  [{s['id']}] {kind} {s['start']:.1f}s {s['idea']} {'(완료)' if s['image'] else '(실패)'}")
    return scenes
