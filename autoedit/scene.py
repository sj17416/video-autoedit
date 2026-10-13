"""나라별 배경 합성 — 사람만 오려서 그 나라 명소 사진 위에 얹는다. 비용 0원.

AI 이미지 생성을 쓰지 않는다. 하는 일은 세 가지뿐이다.
  1. 사람 오려내기: u2net_human_seg (onnxruntime, torch 불필요 — 이 PC에서 설치 가능)
  2. 배경 고르기:  assets/backgrounds/<언어>/ 안의 사진 (내가 찍은 여행 사진을 넣어도 된다)
  3. 합치기:      가장자리를 부드럽게, 사람의 색을 배경 쪽으로 살짝 맞춘다

배경 사진은 권리가 확인된 것만 쓴다. 번들 사진의 출처·라이선스는 assets/backgrounds/LICENSES.md.
"""
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MODEL_PATH = MODELS / "u2net_human_seg.onnx"
MODEL_URLS = ["https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx"]
MODEL_MIN_BYTES = 100_000_000      # 정상 파일은 약 176MB. 작으면 LFS 포인터나 오류 페이지다
BG_DIR = ROOT / "assets" / "backgrounds"
BG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
SIZE = 320                          # u2net 입력 크기

_session = None


def _download():
    MODELS.mkdir(exist_ok=True)
    last = None
    for url in MODEL_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "video-autoedit"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if len(data) >= MODEL_MIN_BYTES:
                tmp = MODEL_PATH.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(MODEL_PATH)
                return
            last = f"파일이 너무 작습니다 ({len(data)}바이트)"
        except OSError as e:
            last = str(e)
    raise RuntimeError(f"사람 오려내기 모델을 받지 못했습니다: {last}")


def _model():
    global _session
    if _session is None:
        if not MODEL_PATH.exists() or MODEL_PATH.stat().st_size < MODEL_MIN_BYTES:
            _download()
        import onnxruntime as ort   # cv2·onnxruntime은 ctranslate2를 먼저 불러야 한다 (CLAUDE.md)
        _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    return _session


def person_mask(bgr):
    """사람 부분이 1에 가까운 float32 마스크 (원본과 같은 크기)."""
    import cv2
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    x = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    sess = _model()
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    lo, hi = float(out.min()), float(out.max())
    out = (out - lo) / max(hi - lo, 1e-6)
    return cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1).astype(np.float32)


def backgrounds(lang):
    d = BG_DIR / lang
    return sorted(p for p in d.glob("*") if p.suffix.lower() in BG_EXTS) if d.exists() else []


def _cover(bgr, W, H):
    import cv2
    h, w = bgr.shape[:2]
    s = max(W / w, H / h)
    r = cv2.resize(bgr, (max(W, round(w * s)), max(H, round(h * s))), interpolation=cv2.INTER_AREA)
    y0, x0 = (r.shape[0] - H) // 2, (r.shape[1] - W) // 2
    return r[y0:y0 + H, x0:x0 + W]


def _match_color(person, bg, mask, amount):
    """사람의 색 평균·퍼짐을 배경 쪽으로 amount만큼 당긴다 (Lab). 합성한 티를 줄인다."""
    import cv2
    if amount <= 0:
        return person
    p = cv2.cvtColor(person, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(bg, cv2.COLOR_BGR2LAB).astype(np.float32)
    m = mask > 0.5
    if m.sum() < 100:
        return person
    for c in range(3):
        pm, ps = p[..., c][m].mean(), p[..., c][m].std() + 1e-6
        bm, bs = b[..., c].mean(), b[..., c].std() + 1e-6
        target = (p[..., c] - pm) * (bs / ps) + bm
        p[..., c] = p[..., c] * (1 - amount) + target * amount
    return cv2.cvtColor(p.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def replace_background(bgr, bg_path, mask=None, match=0.18, feather=1.6):
    """사진 속 사람을 bg_path 사진 위에 얹어 같은 크기의 BGR로 돌려준다.
    mask를 이미 구했다면 넘겨 받아 다시 계산하지 않는다."""
    import cv2
    h, w = bgr.shape[:2]
    if mask is None:
        mask = person_mask(bgr)
    data = np.fromfile(str(bg_path), np.uint8)       # 한글 경로 대비 (CLAUDE.md)
    bg = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bg is None:
        raise RuntimeError(f"배경 사진을 읽을 수 없습니다: {bg_path}")
    bg = _cover(bg, w, h)
    # 가장자리: 살짝 안쪽으로 줄이고 부드럽게 — 원래 배경색이 테두리에 묻어나는 것을 줄인다
    m = cv2.erode((mask > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(np.float32)
    m = np.maximum(m * 0.0 + mask * (mask > 0.5), m)
    m = cv2.GaussianBlur(m, (0, 0), feather).clip(0, 1)
    person = _match_color(bgr, bg, mask, match)
    out = person.astype(np.float32) * m[..., None] + bg.astype(np.float32) * (1 - m[..., None])
    return out.clip(0, 255).astype(np.uint8)
