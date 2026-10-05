"""설명 이미지 생성 (Claude가 그린 SVG → PNG).

- object: 투명 배경 스티커 (아이콘/로고 대용) → 영상 위에 오버레이
- card:   배경이 있는 작은 일러스트 → 화면 중앙 카드
style_refs/ 폴더의 이미지를 그림체 참고용으로 Claude에게 함께 보낸다.
scenes/<id>.png 를 직접 만든 그림(투명 PNG 가능)으로 바꿔 넣으면 그대로 쓴다.
"""
import base64
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def draw_one(scene, scenes_dir, video_size, refs, engine="claude", quality="medium"):
    jobctl.check()
    scenes_dir.mkdir(parents=True, exist_ok=True)
    png_path = scenes_dir / f"{scene['id']}.png"
    if engine == "openai":
        png_path.write_bytes(imagegen.generate(scene, refs, quality))
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


MAX_PARALLEL = 10  # 한 번에 보낼 그림 요청 수 (요청 한도에 걸리면 클라이언트가 알아서 기다렸다 재시도)


def make_illustrations(scenes, scenes_dir, video_size, ref_dir, engine="auto", quality="medium"):
    """이미지가 없는 장면을 모두 동시에 그린다. 다 그린 장면부터 바로 표시."""
    todo = [s for s in scenes if s.get("enabled", True) and not (s.get("image") and (scenes_dir / s["image"]).exists())]
    if not todo:
        print("  그릴 장면 없음 (모두 완료)")
        return scenes
    refs = load_refs(ref_dir)
    engine = resolve_engine(engine)
    name = f"OpenAI {imagegen.MODEL} · 화질 {quality}" if engine == "openai" else "Claude SVG"
    workers = min(len(todo), MAX_PARALLEL)
    print(f"  {len(todo)}개 장면을 {name}로 동시에 {workers}장씩 그리는 중... (그림체 참고 이미지 {len(refs)}장)")
    t0, done, first_error = time.time(), 0, None
    with ThreadPoolExecutor(workers) as ex:
        futures = {ex.submit(draw_one, s, scenes_dir, video_size, refs, engine, quality): s for s in todo}
        for f in as_completed(futures):  # 먼저 끝난 그림부터
            s = futures[f]
            try:
                s["image"] = f.result()
            except Exception as e:  # 한 장이 실패해도 나머지(이미 비용이 든 그림)는 살린다
                s["image"] = None
                first_error = first_error or e
                print(f"  ! [{s['id']}] 실패: {e}")
            done += 1
            kind = "오브젝트" if s["kind"] == "object" else "카드"
            print(f"  ({done}/{len(todo)}, {time.time() - t0:.0f}초) [{s['id']}] {kind} {s['idea']} "
                  f"{'완료' if s['image'] else '실패'}")
    ok = sum(1 for s in todo if s["image"])
    print(f"  그림 {ok}/{len(todo)}장 완료: {time.time() - t0:.0f}초")
    if first_error:  # 성공한 그림은 호출한 쪽에서 저장한 뒤 오류를 알린다
        raise first_error
    return scenes
