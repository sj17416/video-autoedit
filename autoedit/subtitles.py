"""자막: 단어 타이밍으로 자막 조각(cue) 만들기, 영상에 입히기(번인), SRT 저장."""
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import cutplan

FONT_CANDIDATES = ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"]


def build_cues(transcript, timeline, portrait=False):
    """필러를 뺀 단어들을 편집본 시간으로 옮기고, 읽기 좋은 길이로 끊는다."""
    max_chars = 15 if portrait else 24
    items = []
    for w in cutplan.load_words(transcript):
        if w["filler"]:
            continue
        s, e = timeline.to_new(w["start"]), timeline.to_new(w["end"])
        if e - s < 0.02:  # 컷으로 사라진 단어
            continue
        items.append((s, e, w["word"], w["seg"]))

    cues, cur = [], None
    for s, e, word, seg in items:
        attach = not cutplan.norm(word)  # "%" 같은 기호는 앞에 붙임
        if cur is not None:
            sep = "" if attach else " "
            new_text = cur["text"] + sep + word
            text = cur["text"]
            should_break = not attach and (
                s - cur["end"] > 0.5
                or re.search(r"[.?!]$", text)
                or (text.endswith(",") and len(text) >= max_chars * 0.5)
                or len(new_text) > max_chars
                or e - cur["start"] > 4.5
                or (seg != cur["seg"] and s - cur["end"] > 0.25)
            )
            if not should_break:
                cur.update(text=new_text, end=e)
                continue
            cues.append(cur)
        cur = {"start": s, "end": e, "text": word, "seg": seg}
    if cur:
        cues.append(cur)

    out = []
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else c["end"] + 10
        end = min(c["end"] + 0.2, nxt - 0.03)
        if end - c["start"] < 0.7:
            end = min(c["start"] + 0.7, nxt - 0.03)
        text = re.sub(r"\s+", " ", c["text"]).strip().rstrip(".,")
        if text:
            out.append({"start": round(c["start"], 2), "end": round(max(end, c["start"] + 0.3), 2), "text": text})
    return out


def write_srt(cues, path):
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    body = "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n" for i, c in enumerate(cues, 1))
    Path(path).write_text(body, encoding="utf-8-sig")


class SubtitleOverlay:
    """자막 이미지를 미리 만들어 두고 프레임에 합성. style: outline(외곽선) / box(반투명 상자)."""

    def __init__(self, cues, size, style="outline", scale=1.0):
        W, H = size
        self.size = size
        portrait = W < H
        self.fs = max(16, int((W * 0.058 if portrait else H * 0.052) * scale))
        font_path = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
        self.font = ImageFont.truetype(font_path, self.fs) if font_path else ImageFont.load_default(self.fs)
        self.style = style
        self.cy = H * (0.80 if portrait else 0.905)
        self.max_w = W * 0.9
        self.cues = sorted(cues, key=lambda c: c["start"])
        self.starts = [c["start"] for c in self.cues]
        self.cache = {}

    @property
    def top(self):
        """자막이 차지하는 영역의 위쪽 y (다른 그림이 이 아래로 내려오지 않게)."""
        return self.cy - self.fs * 2.0

    def _wrap(self, text):
        if self.font.getlength(text) <= self.max_w or " " not in text:
            return [text]
        words = text.split(" ")
        best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
        return [" ".join(words[:best]), " ".join(words[best:])]

    def sprite(self, text):
        if text in self.cache:
            return self.cache[text]
        lines = self._wrap(text)
        fs = self.fs
        sw = max(2, int(fs * 0.11))
        pad = int(fs * 0.45)
        line_h = int(fs * 1.25)
        widths = [int(self.font.getlength(l)) for l in lines]
        w, h = max(widths) + pad * 2 + sw * 2, line_h * len(lines) + pad * 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if self.style == "box":
            ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=int(fs * 0.3), fill=(0, 0, 0, 165))
        else:  # 부드러운 그림자
            shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(shadow)
            for i, l in enumerate(lines):
                x = (w - widths[i]) / 2
                d.text((x, pad + i * line_h + fs * 0.06), l, font=self.font, fill=(0, 0, 0, 150),
                       stroke_width=sw + 1, stroke_fill=(0, 0, 0, 150))
            img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(fs * 0.08)))
        d = ImageDraw.Draw(img)
        for i, l in enumerate(lines):
            x = (w - widths[i]) / 2
            if self.style == "box":
                d.text((x, pad + i * line_h), l, font=self.font, fill=(255, 255, 255, 255))
            else:
                d.text((x, pad + i * line_h), l, font=self.font, fill=(255, 255, 255, 255),
                       stroke_width=sw, stroke_fill=(20, 20, 20, 255))
        arr = np.array(img)[:, :, [2, 1, 0, 3]].copy()  # RGBA → BGRA
        self.cache[text] = arr
        return arr

    def apply(self, frame, t):
        import bisect
        from .render import blend
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.cues[i]["end"]:
            return frame
        sp = self.sprite(self.cues[i]["text"])
        if not frame.flags.writeable:
            frame = frame.copy()
        blend(frame, sp, self.size[0] / 2, self.cy - sp.shape[0] / 2 + self.fs * 0.6)
        return frame
