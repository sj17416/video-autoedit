"""얼굴 보정 — 눈 크게 · 코 오똑 · 턱선 정리 · 피부 보정. 전부 이 PC에서 계산만 하므로 비용 0원.

AI로 새로 그리는 게 아니라 **사진을 밀고 당기는** 방식이다 (포토샵의 '픽셀 유동화'와 같은 원리).
그래서 인터넷도, API도 쓰지 않고, 몇 장을 만들든 돈이 들지 않는다.

얼굴 위치는 이미 쓰고 있는 YuNet 검출기가 알려준다 (눈 2 · 코끝 · 입꼬리 2, 모두 5점).
5점만으로도 눈·코·턱 보정에 필요한 얼굴 기준선(눈 사이 거리, 기울기, 턱 추정 위치)을 잡을 수 있다.

얼굴을 못 찾으면(옆모습·아주 작은 얼굴·선글라스) 원본을 그대로 돌려주고 found=False로 알린다.
"""
import contextlib
import io
import threading

import cv2
import numpy as np

# 보정 세기 (0 = 원본). 화면의 슬라이더 값이 그대로 들어온다.
DEFAULTS = {
    "eye": 0.0,      # 눈 크기      0~1
    "nose": 0.0,     # 코 날렵하게   0~1
    "chin": 0.0,     # 턱선·목살     0~1
    "skin": 0.0,     # 피부 보정     0~1
    "bright": 0.0,   # 얼굴 밝기    -1~1
}
MAX_FACES = 8        # 한 사진에서 보정할 얼굴 수 (단체 사진 보호)

_det = None
_lock = threading.Lock()


def _detector():
    """YuNet 검출기 (영상 렌더링과 따로 쓴다 — OpenCV 검출기는 여러 스레드가 함께 쓰면 안전하지 않다)."""
    global _det
    if _det is None:
        try:
            from . import render
            if not render.FACE_MODEL.exists():
                with contextlib.redirect_stdout(io.StringIO()):
                    render._face_detector()          # 모델 내려받기는 영상 쪽 함수가 한다
            _det = cv2.FaceDetectorYN.create("onnx", np.fromfile(render.FACE_MODEL, np.uint8),
                                             np.array([], np.uint8), (320, 320), 0.6)
        except Exception:
            _det = False
    return _det


def faces(bgr, width=640):
    """얼굴 목록. 각 얼굴은 {box, eye_r, eye_l, nose, mouth_r, mouth_l, score} (원본 좌표)."""
    det = _detector()
    if not det:
        return []
    h, w = bgr.shape[:2]
    sw = min(width, w)
    small = np.ascontiguousarray(cv2.resize(bgr, (sw, max(1, round(sw * h / w)))))
    with _lock:
        det.setInputSize((small.shape[1], small.shape[0]))
        _, found = det.detect(small)
    if found is None:
        return []
    k = w / sw
    out = []
    for r in found[:MAX_FACES]:
        p = (r[4:14].reshape(5, 2) * k).astype(np.float32)
        out.append({"box": (r[:4] * k).astype(np.float32), "eye_r": p[0], "eye_l": p[1], "nose": p[2],
                    "mouth_r": p[3], "mouth_l": p[4], "score": float(r[14])})
    return out


# ---------------------------------------------------------------- 밀고 당기기 (워프)

# ---------------------------------------------------------------- 나라별 분위기
# 같은 사진을 나라마다 조금씩 다르게 보이게 한다 (색감·밝기·대비·확대·미세 회전).
# 새 배경을 만들어 내는 게 아니라 있는 사진을 손보는 것이므로 역시 비용 0원.
#   warm  따뜻하게(+)/차갑게(-)   bright 밝기   contrast 대비   sat 채도
#   zoom  확대(1.0=그대로)        tilt   미세 회전(도)   vignette 가장자리 어둡게
MOODS = {
    "ko": {"name": "한국 — 밝고 맑게", "warm": 0.10, "bright": 0.06, "contrast": 0.04, "sat": 0.06,
           "zoom": 1.00, "tilt": 0.0, "vignette": 0.00},
    "ja": {"name": "일본 — 차분하고 부드럽게", "warm": -0.06, "bright": 0.04, "contrast": -0.04, "sat": -0.10,
           "zoom": 1.03, "tilt": -0.5, "vignette": 0.06},
    "en": {"name": "영어권 — 또렷하고 선명하게", "warm": 0.00, "bright": 0.02, "contrast": 0.12, "sat": 0.08,
           "zoom": 1.05, "tilt": 0.0, "vignette": 0.10},
    "fr": {"name": "프랑스 — 차분한 필름 느낌", "warm": 0.04, "bright": 0.00, "contrast": -0.02, "sat": -0.06,
           "zoom": 1.02, "tilt": 0.6, "vignette": 0.14},
    "es": {"name": "스페인 — 따뜻하고 진하게", "warm": 0.16, "bright": 0.03, "contrast": 0.08, "sat": 0.14,
           "zoom": 1.04, "tilt": -0.8, "vignette": 0.08},
    "pt": {"name": "포르투갈·브라질 — 화사하게", "warm": 0.13, "bright": 0.07, "contrast": 0.06, "sat": 0.16,
           "zoom": 1.01, "tilt": 0.4, "vignette": 0.04},
    "de": {"name": "독일 — 깔끔하고 차갑게", "warm": -0.10, "bright": 0.01, "contrast": 0.10, "sat": -0.04,
           "zoom": 1.06, "tilt": 0.0, "vignette": 0.12},
}


def mood(bgr, lang, amount=1.0):
    """나라별 분위기를 입힌다. amount 0이면 아무것도 하지 않는다."""
    m = MOODS.get(lang)
    if not m or amount <= 0:
        return bgr
    a = max(0.0, min(1.0, float(amount)))
    out = bgr.astype(np.float32)

    if abs(m["warm"]) > 0.001:                       # 따뜻하게 = 빨강↑ 파랑↓
        w = m["warm"] * a
        out[:, :, 2] *= 1 + w * 0.5
        out[:, :, 0] *= 1 - w * 0.4
    if abs(m["contrast"]) > 0.001:
        out = (out - 128) * (1 + m["contrast"] * a) + 128
    if abs(m["bright"]) > 0.001:
        out += m["bright"] * a * 255 * 0.25
    out = np.clip(out, 0, 255)
    if abs(m["sat"]) > 0.001:
        hsv = cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (1 + m["sat"] * a), 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)

    h, w = out.shape[:2]
    if abs(m["vignette"]) > 0.001:                   # 가장자리를 살짝 어둡게
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xs - w / 2) / (w / 2)) ** 2 + ((ys - h / 2) / (h / 2)) ** 2)
        out *= (1 - m["vignette"] * a * np.clip(r - 0.55, 0, None) / 0.8)[:, :, None]
    out = np.clip(out, 0, 255).astype(np.uint8)

    zoom, tilt = 1 + (m["zoom"] - 1) * a, m["tilt"] * a
    if abs(zoom - 1) > 0.001 or abs(tilt) > 0.01:    # 확대 + 미세 회전 (구도를 조금 바꾼다)
        mat = cv2.getRotationMatrix2D((w / 2, h / 2), tilt, max(zoom, 1.0))
        out = cv2.warpAffine(out, mat, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return out


def _grid(shape):
    h, w = shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return xs, ys


def _bulge(xs, ys, mx, my, center, radius, amount):
    """center 둘레 radius 안을 부풀린다(amount>0). 눈 크게 만들 때 쓴다.

    remap은 '결과의 이 자리에 원본의 어디를 가져올까'를 묻는다. 중심 쪽에서 가져오면 커 보인다.
    """
    if amount <= 0 or radius <= 1:
        return
    cx, cy = float(center[0]), float(center[1])
    x0, x1 = max(0, int(cx - radius)), min(xs.shape[1], int(cx + radius) + 1)
    y0, y1 = max(0, int(cy - radius)), min(xs.shape[0], int(cy + radius) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    dx, dy = xs[y0:y1, x0:x1] - cx, ys[y0:y1, x0:x1] - cy
    dist = np.sqrt(dx * dx + dy * dy)
    t = np.clip(1 - dist / radius, 0, 1)          # 가장자리로 갈수록 0 (자연스럽게 사라지게)
    scale = 1 - amount * t * t                     # 중심일수록 많이 당겨 온다
    mx[y0:y1, x0:x1] = cx + dx * scale
    my[y0:y1, x0:x1] = cy + dy * scale


def _pinch_x(xs, ys, mx, my, center, half_w, half_h, amount, axis_x=None):
    """타원 영역 안을 가로로 좁힌다. 코·턱선을 날렵하게 만들 때 쓴다."""
    if amount <= 0 or half_w <= 1 or half_h <= 1:
        return
    cx, cy = float(center[0]), float(center[1])
    ax = float(cx if axis_x is None else axis_x)
    x0, x1 = max(0, int(cx - half_w)), min(xs.shape[1], int(cx + half_w) + 1)
    y0, y1 = max(0, int(cy - half_h)), min(xs.shape[0], int(cy + half_h) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    dx, dy = xs[y0:y1, x0:x1] - cx, ys[y0:y1, x0:x1] - cy
    t = np.clip(1 - ((dx / half_w) ** 2 + (dy / half_h) ** 2), 0, 1)
    # 결과 x를 바깥에서 가져오면 좁아 보인다
    mx[y0:y1, x0:x1] = ax + (xs[y0:y1, x0:x1] - ax) * (1 + amount * t)
    my[y0:y1, x0:x1] = ys[y0:y1, x0:x1]


def _lift_y(xs, ys, mx, my, center, half_w, half_h, amount):
    """세로로 살짝 늘려 콧대가 서 보이게 한다."""
    if amount <= 0 or half_w <= 1 or half_h <= 1:
        return
    cx, cy = float(center[0]), float(center[1])
    x0, x1 = max(0, int(cx - half_w)), min(xs.shape[1], int(cx + half_w) + 1)
    y0, y1 = max(0, int(cy - half_h)), min(xs.shape[0], int(cy + half_h) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    dx, dy = xs[y0:y1, x0:x1] - cx, ys[y0:y1, x0:x1] - cy
    t = np.clip(1 - ((dx / half_w) ** 2 + (dy / half_h) ** 2), 0, 1)
    my[y0:y1, x0:x1] = cy + dy * (1 - amount * t * 0.5)


def _warp_face(bgr, f, s):
    """한 얼굴에 눈·코·턱 보정을 적용한 지도를 만들어 되돌린다."""
    xs, ys = _grid(bgr.shape)
    mx, my = xs.copy(), ys.copy()
    er, el, nose = f["eye_r"], f["eye_l"], f["nose"]
    mouth = (f["mouth_r"] + f["mouth_l"]) / 2
    eye_c = (er + el) / 2
    D = float(np.linalg.norm(el - er)) or 1.0      # 눈 사이 거리 = 얼굴 크기의 기준자

    if s["eye"] > 0:                                # 눈: 양쪽을 같은 양만큼 부풀린다
        r = D * 0.42
        for e in (er, el):
            _bulge(xs, ys, mx, my, e, r, s["eye"] * 0.22)

    if s["nose"] > 0:                               # 코: 가로로 좁히고 세로로 살짝 당긴다
        _pinch_x(xs, ys, mx, my, nose, D * 0.34, D * 0.40, s["nose"] * 0.20)
        _lift_y(xs, ys, mx, my, nose, D * 0.30, D * 0.42, s["nose"] * 0.10)

    if s["chin"] > 0:                               # 턱·목살: 입 아래쪽을 안쪽으로 당긴다
        chin = mouth + (mouth - eye_c) * 0.85
        _pinch_x(xs, ys, mx, my, (nose + chin) / 2, D * 1.15, D * 1.05, s["chin"] * 0.17,
                 axis_x=float(nose[0]))
        _pinch_x(xs, ys, mx, my, chin + (chin - mouth) * 0.5, D * 1.0, D * 0.70, s["chin"] * 0.13,
                 axis_x=float(nose[0]))
    return mx, my


def _skin_mask(shape, f, D):
    """얼굴 영역만 부드럽게 (배경·머리카락까지 뭉개지지 않게)."""
    m = np.zeros(shape[:2], np.float32)
    eye_c = (f["eye_r"] + f["eye_l"]) / 2
    mouth = (f["mouth_r"] + f["mouth_l"]) / 2
    c = (eye_c + mouth) / 2
    cv2.ellipse(m, (int(c[0]), int(c[1])), (int(D * 1.05), int(D * 1.45)), 0, 0, 360, 1.0, -1)
    k = max(3, int(D * 0.5) | 1)
    return cv2.GaussianBlur(m, (k, k), 0)


def retouch(bgr, settings=None, strength=1.0):
    """얼굴 보정을 적용한 BGR 이미지와 정보를 돌려준다.

    반환: (결과 이미지, {"found": 얼굴 수, "applied": 실제로 보정했는지})
    """
    s = {**DEFAULTS, **(settings or {})}
    # 화면에서 어떤 값이 와도 안전한 범위로 (bright만 -1~1, 나머지는 0~1)
    s = {k: max(-1.0, min(1.0, float(v or 0))) if k == "bright" else max(0.0, min(1.0, float(v or 0)))
         for k, v in s.items() if k in DEFAULTS}
    s = {k: (v * strength if k != "bright" else v) for k, v in s.items()}
    if all(abs(v) < 0.01 for v in s.values()):
        return bgr, {"found": None, "applied": False}
    found = faces(bgr)
    if not found:
        return bgr, {"found": 0, "applied": False}

    out = bgr
    if any(s[k] > 0 for k in ("eye", "nose", "chin")):
        for f in found:
            mx, my = _warp_face(out, f, s)
            out = cv2.remap(out, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    if s["skin"] > 0 or abs(s["bright"]) > 0.01:
        smooth = out
        if s["skin"] > 0:
            # 질감을 남기면서 잡티만 눕힌다 (bilateral은 가장자리를 지키며 부드럽게 한다)
            d = max(5, int(np.linalg.norm(found[0]["eye_l"] - found[0]["eye_r"]) * 0.18) | 1)
            smooth = cv2.bilateralFilter(out, d, 45, 45)
        mask = np.zeros(out.shape[:2], np.float32)
        for f in found:
            D = float(np.linalg.norm(f["eye_l"] - f["eye_r"])) or 1.0
            mask = np.maximum(mask, _skin_mask(out.shape, f, D))
        a = (mask * s["skin"] * 0.75)[:, :, None]
        out = (out * (1 - a) + smooth * a).astype(np.uint8)
        if abs(s["bright"]) > 0.01:                  # 얼굴만 살짝 밝게/어둡게
            b = (mask * s["bright"] * 26)[:, :, None]
            out = np.clip(out.astype(np.float32) + b, 0, 255).astype(np.uint8)
    return out, {"found": len(found), "applied": True}
