"""예능 효과 (흑백요리사 스타일).

- pop      : 노란 굵은 강조 자막이 통통 튀어나옴 ("킹정ㅋㅋ")
- question : 흰 자막이 흔들림 ("?? 실화냐")
- dramatic : 화면이 순간 흑백 + 비네팅, 검은 띠 위 흰 자막, 묵직한 "둥" (흑백요리사 시그니처 연출)
- zoom     : 반응 순간 얼굴 쪽으로 빠르게 줌인
효과음은 파일 없이 직접 합성한다 (뿅 / 띠용 / 둥 / 휙).
"""
import bisect
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .media import SR

FONT = next((f for f in ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"] if Path(f).exists()), None)
KINDS = ("pop", "question", "dramatic", "zoom")


def _ease_out_back(x):
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


# ---------------------------------------------------------------- 자막 그림

def _text_sprite(text, fs, fill, stroke, stroke_w, angle=0.0, band=False, W=None):
    font = ImageFont.truetype(FONT, fs) if FONT else ImageFont.load_default(fs)
    tw = int(font.getlength(text))
    pad = int(fs * 0.5)
    w, h = tw + pad * 2 + stroke_w * 2, int(fs * 1.5) + pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((pad + fs * 0.06, pad * 0.5 + fs * 0.08), text, font=font, fill=(0, 0, 0, 170),
                                stroke_width=stroke_w, stroke_fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(fs * 0.06)))
    ImageDraw.Draw(img).text((pad, pad * 0.5), text, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke)
    if band:  # 흑백요리사 느낌: 화면 폭 검은 띠 + 위아래 흰 선
        bw = W or w
        bar = Image.new("RGBA", (bw, h + int(fs * 0.5)), (10, 10, 10, 215))
        d = ImageDraw.Draw(bar)
        lw = max(2, fs // 18)
        d.rectangle([0, 0, bw, lw], fill=(240, 240, 240, 255))
        d.rectangle([0, bar.height - lw, bw, bar.height], fill=(240, 240, 240, 255))
        bar.alpha_composite(img, ((bw - w) // 2, int(fs * 0.25)))
        img = bar
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    return np.array(img)[:, :, [2, 1, 0, 3]].copy()


class EffectsOverlay:
    def __init__(self, effects, size):
        self.size = size
        W, H = size
        fs = int(min(W, H) * 0.085)
        self.items = []
        for e in sorted((e for e in effects if e.get("enabled", True)), key=lambda e: e["start"]):
            kind, text = e["type"], (e.get("text") or "").strip()
            sprite = None
            if text and kind == "pop":
                sprite = _text_sprite(text, fs, (255, 225, 77, 255), (20, 20, 20, 255), max(4, fs // 7), angle=4)
            elif text and kind == "question":
                sprite = _text_sprite(text, fs, (255, 255, 255, 255), (20, 20, 20, 255), max(4, fs // 7), angle=-3)
            elif text and kind == "dramatic":
                sprite = _text_sprite(text, int(fs * 0.9), (255, 255, 255, 255), (0, 0, 0, 255), max(2, fs // 16),
                                      band=True, W=W)
            self.items.append({**e, "sprite": sprite, "focus": None})
        self.starts = [it["start"] for it in self.items]
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2)
        self.vignette = np.clip(1.15 - 0.55 * r ** 2, 0.35, 1.0)[:, :, None]  # 흑백 연출용 가장자리 어둡게

    def _active(self, t):
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.items[i]["end"]:
            return None
        return self.items[i]

    def apply_base(self, frame, t):
        """영상 자체에 거는 효과 (줌, 흑백) — 아이콘·자막보다 먼저."""
        it = self._active(t)
        if not it or it["type"] not in ("zoom", "dramatic"):
            return frame
        W, H = self.size
        el, left = t - it["start"], it["end"] - t
        if it["type"] == "zoom":
            if it["focus"] is None:  # 효과가 시작되는 프레임에서 가장 큰 얼굴 쪽으로
                from .render import detect_faces
                faces = detect_faces(frame)
                if faces:
                    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                    it["focus"] = (x + w / 2, y + h / 2)
                else:
                    it["focus"] = (W / 2, H * 0.42)
            k = min(1.0, el / 0.18, left / 0.22)
            z = 1 + 0.16 * (1 - (1 - max(0.0, k)) ** 3)
            cw, ch = W / z, H / z
            fx, fy = it["focus"]
            x0 = min(max(fx - cw / 2, 0), W - cw)
            y0 = min(max(fy - ch / 2, 0), H - ch)
            return cv2.resize(frame[int(y0):int(y0 + ch), int(x0):int(x0 + cw)], (W, H), interpolation=cv2.INTER_LINEAR)
        # dramatic: 흑백 + 대비 + 비네팅 (0.12초 전환)
        a = min(1.0, el / 0.12, left / 0.12)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.convertScaleAbs(gray, alpha=1.25, beta=-25)
        mono = (cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR).astype(np.float32) * self.vignette)
        out = frame.astype(np.float32) * (1 - a) + mono * a
        return np.clip(out, 0, 255).astype(np.uint8)

    def apply_top(self, frame, t):
        """강조 자막 (아이콘 위, 일반 자막 아래 층)."""
        it = self._active(t)
        if not it or it["sprite"] is None:
            return frame
        from .render import blend
        W, H = self.size
        el, left = t - it["start"], it["end"] - t
        sp = it["sprite"]
        opacity = max(0.0, min(1.0, left / 0.15, el / 0.06))
        if it["type"] == "dramatic":
            scale, cx, cy = 1.0, W / 2, H * 0.2
        else:
            scale = 0.3 + 0.7 * _ease_out_back(min(1.0, el / 0.28))
            cx, cy = W / 2, H * 0.17
            if it["type"] == "question" and el < 0.6:  # 흔들림
                cx += np.sin(el * 55) * W * 0.006 * (1 - el / 0.6)
        if abs(scale - 1) > 1e-3:
            sp = cv2.resize(sp, (max(1, int(sp.shape[1] * scale)), max(1, int(sp.shape[0] * scale))),
                            interpolation=cv2.INTER_LINEAR)
        frame = frame if frame.flags.writeable else frame.copy()
        blend(frame, sp, cx, cy, opacity)
        return frame


# ---------------------------------------------------------------- 효과음 (합성)

def _env(n, attack=0.005, decay=None):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    return a * (np.exp(-t / decay) if decay else 1.0)


def _sfx(kind):
    rng = np.random.default_rng(7)
    if kind == "pop":  # 뿅
        n = int(0.09 * SR)
        f = np.linspace(900, 1700, n)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.03) * 0.35
    if kind == "question":  # 띠용
        n = int(0.32 * SR)
        f = np.linspace(700, 260, n) * (1 + 0.04 * np.sin(np.arange(n) / SR * 2 * np.pi * 18))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.004, 0.16) * 0.3
    if kind == "dramatic":  # 둥
        n = int(0.9 * SR)
        t = np.arange(n) / SR
        boom = (np.sin(2 * np.pi * 52 * t) + 0.5 * np.sin(2 * np.pi * 104 * t)) * np.exp(-t / 0.35)
        hit = rng.standard_normal(n) * np.exp(-t / 0.02) * 0.4
        return (boom + hit) * _env(n, 0.003) * 0.38
    if kind == "zoom":  # 휙
        n = int(0.28 * SR)
        noise = rng.standard_normal(n)
        noise = np.convolve(noise, np.ones(12) / 12, "same")  # 거친 노이즈 → 바람 소리
        shape = np.sin(np.linspace(0, np.pi, n)) ** 2
        return noise * shape * 0.5
    return np.zeros(1)


def mix_sfx(audio, effects):
    """편집본 오디오(int16 스테레오)에 효과음을 섞는다."""
    out = audio.astype(np.float32) / 32768
    for e in effects:
        if not e.get("enabled", True):
            continue
        s = _sfx(e["type"])
        a = int(e["start"] * SR)
        b = min(len(out), a + len(s))
        if b > a:
            out[a:b] += s[: b - a, None] * 0.75  # 말소리를 덮지 않도록 살짝 작게
    return (np.clip(out, -1, 1) * 32767).astype(np.int16)
