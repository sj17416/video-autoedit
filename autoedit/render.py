"""최종 렌더: 컷 적용 + 설명 이미지 삽입 + 모자이크 + 자막 + 개인정보 음성 삐 처리."""
import bisect
import os
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from . import jobctl
from .media import SR, FrameWriter, iter_frames, pixelate, write_wav

FADE = 0.25      # 설명 이미지 페이드 인/아웃(초)
ZOOM = 0.04      # 카드에 주는 은은한 줌인
JOIN_FADE = 0.006  # 컷 이음새 '딱' 소리 방지용 오디오 페이드(초)


def bleep(audio, spans, mode="beep"):
    """원본 오디오의 해당 구간을 1kHz 삐 소리(beep) 또는 무음(mute)으로 교체."""
    for s, e in spans:
        a, b = max(0, int(s * SR)), min(len(audio), int(e * SR))
        if b > a:
            if mode == "mute":
                audio[a:b] = 0
            else:
                tone = (np.sin(2 * np.pi * 1000 * np.arange(b - a) / SR) * 0.25 * 32767).astype(np.int16)
                audio[a:b] = tone[:, None]
    return audio


def cut_audio(audio, timeline):
    spf = SR // timeline.fps
    fade_n = int(JOIN_FADE * SR)
    ramp = np.linspace(0, 1, fade_n, dtype=np.float32)[:, None]
    pieces = []
    for fs, fe in timeline.segs:
        piece = audio[fs * spf: fe * spf].astype(np.float32)
        if len(piece) < fe * spf - fs * spf:  # 오디오가 영상보다 짧으면 무음으로 채움
            piece = np.vstack([piece, np.zeros((fe * spf - fs * spf - len(piece), 2), np.float32)])
        if len(piece) > 2 * fade_n:
            piece[:fade_n] *= ramp
            piece[-fade_n:] *= ramp[::-1]
        pieces.append(piece)
    out = np.vstack(pieces) if pieces else np.zeros((0, 2), np.float32)
    return np.clip(out, -32768, 32767).astype(np.int16)


def load_rgba(path):
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_UNCHANGED)  # 한글 경로 대응
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def blend(frame, rgba, cx, cy, opacity=1.0):
    """frame(BGR) 위 (cx, cy) 중심에 rgba를 알파 합성."""
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    src = rgba[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = src[:, :, 3:4].astype(np.float32) * (opacity / 255)
    roi = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    frame[fy0:fy1, fx0:fx1] = (src[:, :, :3] * a + roi * (1 - a)).astype(np.uint8)


def make_card(img, size, max_h=None):
    """일러스트를 둥근 모서리 + 흰 테두리 + 그림자가 있는 카드(RGBA)로."""
    W, H = size
    s = min(W * 0.62 / img.shape[1], (max_h or H * 0.70) / img.shape[0])
    art = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=cv2.INTER_AREA)
    h, w = art.shape[:2]
    border, pad = max(6, int(min(W, H) * 0.008)), int(min(W, H) * 0.04)
    r = int(min(w, h) * 0.06)
    cw, ch = w + 2 * border, h + 2 * border
    card = np.zeros((ch + 2 * pad, cw + 2 * pad, 4), np.uint8)

    def rounded_mask(width, height, radius):
        m = np.zeros((height, width), np.uint8)
        cv2.rectangle(m, (radius, 0), (width - radius, height), 255, -1)
        cv2.rectangle(m, (0, radius), (width, height - radius), 255, -1)
        for x, y in ((radius, radius), (width - radius - 1, radius), (radius, height - radius - 1),
                     (width - radius - 1, height - radius - 1)):
            cv2.circle(m, (x, y), radius, 255, -1, cv2.LINE_AA)
        return m

    outer = rounded_mask(cw, ch, r + border)
    shadow = np.zeros(card.shape[:2], np.uint8)
    off = int(pad * 0.35)
    shadow[pad + off:pad + off + ch, pad:pad + cw] = outer
    shadow = cv2.GaussianBlur(shadow, (0, 0), pad / 3)
    card[:, :, 3] = (shadow * 0.45).astype(np.uint8)
    # 흰 테두리
    sl = (slice(pad, pad + ch), slice(pad, pad + cw))
    card[sl][outer > 0] = (255, 255, 255, 255)
    # 그림 (안쪽 둥근 마스크)
    inner = rounded_mask(w, h, r)
    region = card[pad + border:pad + border + h, pad + border:pad + border + w]
    a = (art[:, :, 3:4].astype(np.float32) / 255) * (inner[:, :, None] / 255)
    region[:, :, :3] = (art[:, :, :3] * a + region[:, :, :3] * (1 - a)).astype(np.uint8)
    return card


def ease_out_back(x):
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out(x):
    return 1 - (1 - x) ** 3


ANCHORS = {"left": (0.2, 0.45), "right": (0.8, 0.45), "top": (0.5, 0.2), "bottom": (0.5, 0.78),
           "top-left": (0.2, 0.25), "top-right": (0.8, 0.25), "bottom-left": (0.2, 0.72),
           "bottom-right": (0.8, 0.72), "center": (0.5, 0.45)}
_face = None


FACE_MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
FACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def _face_detector():
    """OpenCV YuNet 얼굴 검출기 (모델 230KB, 처음 한 번 다운로드). 실패하면 False."""
    global _face
    if _face is None:
        try:
            if not FACE_MODEL.exists():
                FACE_MODEL.parent.mkdir(exist_ok=True)
                FACE_MODEL.write_bytes(urllib.request.urlopen(FACE_URL, timeout=30).read())
            # 한글 경로를 OpenCV가 못 여므로 모델을 메모리로 읽어서 넘긴다
            _face = cv2.FaceDetectorYN.create("onnx", np.fromfile(FACE_MODEL, np.uint8), np.array([], np.uint8),
                                              (320, 320), 0.7)
        except Exception as e:
            print(f"  ! 얼굴 검출기를 쓸 수 없어 오브젝트를 오른쪽에 둡니다: {e}")
            _face = False
    return _face


def auto_position(frame):
    """얼굴이 없는 쪽으로 배치."""
    H, W = frame.shape[:2]
    small = cv2.resize(frame, (640, int(640 * H / W)))
    faces = []
    det = _face_detector()
    if det:
        det.setInputSize((small.shape[1], small.shape[0]))
        _, found = det.detect(small)
        faces = [] if found is None else [f[:4] for f in found]
    big = max(faces, key=lambda f: f[2] * f[3]) if len(faces) else None
    if W < H:  # 세로 영상(쇼츠)은 위/아래
        return "bottom" if big is not None and big[1] < small.shape[0] * 0.4 else "top"
    if big is None:
        return "right"
    return "right" if (big[0] + big[2] / 2) / small.shape[1] < 0.5 else "left"


def cover(img, size):
    """화면을 꽉 채우도록 잘라서 맞춤 (전체 화면 모드)."""
    W, H = size
    s = max(W / img.shape[1], H / img.shape[0])
    r = cv2.resize(img, (int(img.shape[1] * s) + 1, int(img.shape[0] * s) + 1), interpolation=cv2.INTER_AREA)
    y0, x0 = (r.shape[0] - H) // 2, (r.shape[1] - W) // 2
    out = r[y0:y0 + H, x0:x0 + W].copy()
    out[:, :, 3] = 255
    return out


class SceneOverlay:
    def __init__(self, scenes, scenes_dir, size, card_dim=0.35, bottom_limit=None, card_mode="card"):
        """bottom_limit: 이 y 아래(자막 영역)로는 그림이 내려오지 않게 한다.
        card_mode: card(중앙 카드) / full(전체 화면)."""
        self.size, self.dim, self.card_mode = size, card_dim, card_mode
        W, H = size
        self.top, self.bottom = H * 0.04, (bottom_limit or H * 0.96)
        self.items = []
        for s in scenes:
            if not s.get("enabled", True) or not s.get("image"):
                continue
            img = load_rgba(scenes_dir / s["image"])
            if img is None:
                print(f"  ! 그림을 읽을 수 없어 건너뜀: {s['image']}")
                continue
            if s["kind"] == "card" and card_mode == "full":
                sprite = cover(img, size)
            elif s["kind"] == "card":
                sprite = make_card(img, size, min(H * 0.70, (self.bottom - self.top) * 0.92))
            else:  # 오브젝트: 투명 여백을 잘라내고 화면 크기에 맞춤
                ys, xs = np.nonzero(img[:, :, 3] > 8)
                if len(xs):
                    img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                box = min(W, H) * (0.62 if W < H else 0.42) * s.get("scale", 1.0)
                k = box / max(img.shape[:2])
                sprite = cv2.resize(img, (max(1, int(img.shape[1] * k)), max(1, int(img.shape[0] * k))),
                                    interpolation=cv2.INTER_AREA)
            self.items.append({"s": s["start"], "e": s["end"], "kind": s["kind"],
                               "pos": s.get("position", "auto"), "sprite": sprite})
        self.items.sort(key=lambda x: x["s"])
        self.starts = [it["s"] for it in self.items]

    def apply(self, frame, t):
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.items[i]["e"]:
            return frame
        it = self.items[i]
        W, H = self.size
        el = t - it["s"]
        opacity = max(0.0, min(1.0, el / FADE, (it["e"] - t) / FADE))
        frame = frame.copy()
        if it["kind"] == "card" and self.card_mode == "full":
            scale = 1 + ZOOM * el / max(it["e"] - it["s"], 1e-6)  # 은은한 줌인
            sp = it["sprite"]
            ch, cw = int(H / scale), int(W / scale)
            y0, x0 = (H - ch) // 2, (W - cw) // 2
            art = cv2.resize(sp[y0:y0 + ch, x0:x0 + cw, :3], (W, H), interpolation=cv2.INTER_LINEAR)
            return art if opacity >= 1 else cv2.addWeighted(art, opacity, frame, 1 - opacity, 0)
        if it["kind"] == "card":
            if self.dim > 0:  # 카드 뒤 영상을 살짝 어둡게
                frame = cv2.convertScaleAbs(frame, alpha=1 - self.dim * opacity)
            scale = (0.9 + 0.1 * ease_out(min(1.0, el / 0.35))) * (1 + ZOOM * 0.5 * el / max(it["e"] - it["s"], 1e-6))
            cx, cy = W / 2, min(H / 2, self.bottom - it["sprite"].shape[0] * 0.46)
        else:
            if it["pos"] == "auto":
                it["pos"] = auto_position(frame)
            ax, ay = ANCHORS.get(it["pos"], ANCHORS["right"])
            scale = 0.55 + 0.45 * ease_out_back(min(1.0, el / 0.4))  # 톡 튀어나오는 효과
            sh, sw = it["sprite"].shape[:2]
            cx = min(max(ax * W, sw / 2 + W * 0.03), W - sw / 2 - W * 0.03)
            cy = min(max(ay * H, sh / 2 + self.top), self.bottom - sh / 2)
            cy += np.sin(2 * np.pi * el / 2.6) * H * 0.006  # 살짝 둥실거림
        sp = it["sprite"]
        if abs(scale - 1) > 1e-3:
            sp = cv2.resize(sp, (max(1, int(sp.shape[1] * scale)), max(1, int(sp.shape[0] * scale))),
                            interpolation=cv2.INTER_LINEAR)
        blend(frame, sp, cx, cy, opacity)
        return frame


class FaceMosaic:
    """모든 얼굴 모자이크. 몇 프레임마다 얼굴을 찾고, 사이 프레임은 직전 위치를 쓴다."""

    def __init__(self, size, every=3):
        self.size, self.every, self.n, self.boxes = size, every, 0, []

    def apply(self, frame):
        W, H = self.size
        if self.n % self.every == 0:
            det = _face_detector()
            self.boxes = []
            if det:
                sw = min(960, W)  # 멀리 있는 작은 얼굴도 잡도록 960px에서 검출
                small = cv2.resize(frame, (sw, int(sw * H / W)))
                det.setInputSize((small.shape[1], small.shape[0]))
                _, found = det.detect(small)
                k = W / sw
                for f in ([] if found is None else found):
                    x, y, w, h = (f[:4] * k).astype(int)
                    px, py = int(w * 0.3), int(h * 0.35)
                    self.boxes.append((max(0, x - px), max(0, y - py), min(W, x + w + px), min(H, y + h + py)))
        self.n += 1
        if not self.boxes:
            return frame
        frame = frame if frame.flags.writeable else frame.copy()
        for b in self.boxes:
            pixelate(frame, b, block=max(10, (b[3] - b[1]) // 8))
        return frame


class MosaicBoxes:
    def __init__(self, detections, scan_dt, size, pad=0.15, scale=1.0):
        """scale: OCR 좌표(원본 해상도) → 출력 해상도 배율."""
        w, h = size
        self.dt = scan_dt
        self.items = []
        for d in sorted(detections, key=lambda d: d["t"]):
            x1, y1, x2, y2 = (int(v * scale) for v in d["box"])
            px, py = int((x2 - x1) * pad) + 4, int((y2 - y1) * pad) + 4
            self.items.append((d["t"], (max(0, x1 - px), max(0, y1 - py), min(w, x2 + px), min(h, y2 + py))))
        self.times = [it[0] for it in self.items]

    def boxes_at(self, t):
        # 스캔 간격 사이에 글자가 나타나거나 사라질 수 있으니 앞뒤 스캔 결과를 모두 적용
        lo = bisect.bisect_left(self.times, t - self.dt - 1e-6)
        hi = bisect.bisect_right(self.times, t + self.dt + 1e-6)
        return [self.items[k][1] for k in range(lo, hi)]


def render(src, meta, timeline, audio, out_path, work, *, size=None, speed="fast", scenes=(), scenes_dir=None,
           pii_dets=(), scan_dt=1.0, face_mosaic=False, cues=(), sub_style="outline", sub_scale=1.0,
           card_dim=0.35, card_mode="card", loudnorm=True):
    size = size or (meta["width"], meta["height"])
    fps = timeline.fps
    wav = work / "edited_audio.wav"
    write_wav(wav, cut_audio(audio, timeline))

    from .subtitles import SubtitleOverlay
    subs = SubtitleOverlay(cues, size, sub_style, sub_scale) if cues else None
    overlay = SceneOverlay(scenes, scenes_dir, size, card_dim, subs.top if subs else None, card_mode)
    mosaic = MosaicBoxes(pii_dets, scan_dt, size, scale=size[0] / meta["width"])
    faces = FaceMosaic(size) if face_mosaic else None
    # 렌더링이 끝나기 전까지는 임시 파일에 쓴다 → 실패/중지해도 이전 결과물이 보존됨
    tmp = out_path.with_name(out_path.stem + ".렌더링중.mp4")
    audio_filter = "loudnorm=I=-14:TP=-1.5:LRA=11" if loudnorm else None
    writer = FrameWriter(tmp, fps, size, wav, work / "ffmpeg_encode.log", audio_filter, speed)
    print(f"  출력: {size[0]}x{size[1]}, {fps}fps")
    new_idx, t0, ok = 0, time.time(), False
    try:
        for i, frame in iter_frames(src, fps, size, timeline.segs):
            if not timeline.kept(i):
                continue
            boxes = mosaic.boxes_at(i / fps)
            t_new = new_idx / fps
            if boxes:
                frame = frame.copy()
                for b in boxes:
                    pixelate(frame, b)
            if faces:
                frame = faces.apply(frame)
            frame = overlay.apply(frame, t_new)
            if subs:
                frame = subs.apply(frame, t_new)
            writer.write(frame)
            new_idx += 1
            if new_idx % fps == 0:
                jobctl.check()
            if new_idx % (fps * 5) == 0:
                print(f"\r  렌더링 {new_idx / max(timeline.total_frames, 1) * 100:5.1f}%  ({time.time() - t0:.0f}초)",
                      end="", flush=True)
        ok = True
    finally:
        try:
            writer.close()
        except Exception:
            ok = False
            raise
        finally:
            if ok:
                os.replace(tmp, out_path)
            else:
                tmp.unlink(missing_ok=True)
    took = time.time() - t0
    print(f"\r  렌더링 100.0%  ({took:.0f}초, 영상 길이 대비 {took / max(timeline.total, 0.1):.1f}배)")
