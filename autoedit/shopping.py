"""쇼핑 쇼츠 모드: 직접 찍은 클립 + 대본 → 세로 쇼츠 완성본(1080x1920).

대본 형식 (한 줄 = 한 컷, 위에서부터 순서대로):

    키워드: 쫀득
    아이가 간식 사 달라고 조르는데
    고구마가 남아 있길래
    3_치즈넣기.mp4 | 이걸 넣고 치즈볼을 만들어 봤어요 [카드: 코다노 모짜렐라 / 자연치즈 99.5%]
    레시피 궁금하면 '쫀득' 남겨 주세요

- 맨 앞에 "!"를 붙인 줄은 후킹 자막: 크게, 화면 위쪽에 (첫 1~2초용).
- [카드: 상품 이름 / 한 줄 설명]을 붙인 줄은 그 컷 동안 화면 위쪽에 상품 카드가 뜬다 (읽지는 않는다).

- 클립은 이름 순서대로 줄에 하나씩 붙는다. "파일이름 | 자막"처럼 쓰면 그 클립을 쓴다.
  "a.mp4, b.mp4 | 자막"처럼 여러 개를 적으면 그 줄 동안 화면이 그 클립들로 빠르게 바뀐다.
- 머리 줄(선택): "제목: …"(교육용 제목 띠 + STEP 번호), "배지: 30초 레시피"(왼쪽 위 배지),
  "카운트다운: 켬"(오른쪽 위 큰 숫자가 줄마다 하나씩 줄어든다), "컷길이: 0.9"(한 화면 최대 초),
  "루프: 켬"(끝이 처음으로 끊김 없이 이어지게: 배경음악 이음매를 맞추고 끝에서 줄이지 않는다),
  "자연: 켬"(하얀 번쩍임 대신 손으로 든 듯한 잔잔한 흔들림 → 덜 딱딱하게).
  "스티커: 켬"(제목을 크림색 스티커 + 귀여운 글꼴로, 마지막 단어는 빨강),
  "순서: a.mp4=씻기, b.mp4=볶기, …"(과정 줄마다 위쪽에 동그란 사진 + 화살표로 조리 순서 띠, 지금 단계는 빨강).
- 자막: 화면 가운데보다 살짝 아래, 흰 글자 + 검은 테두리, 한 줄 12자 이내, 키워드는 노란색.
- 컷: 말이 2초를 넘으면 같은 클립의 다른 부분으로 한 번 더 끊어 1~2초마다 화면이 바뀐다.
- 화면: 컷마다 천천히 다가가는 줌, 키워드가 나오는 컷은 빠르게 확 당기는 줌.
- 소리: AI 목소리(edge-tts, 무료, 앞뒤 무음은 잘라 빠르게) + 클립의 실제 소리(작게) + 배경음악(있으면, 더 작게)
  + 컷마다 작은 '휙', 키워드에 '뿅' 효과음 → -14 LUFS.
"""
import asyncio
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import jobctl
from .llm import UserError
from .effects import _sfx
from .media import FFMPEG, SR, probe, run, write_wav
from .subtitles import FONT_CANDIDATES, write_srt

W, H, FPS = 1080, 1920, 30
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
VOICES = {"여성": "ko-KR-SunHiNeural", "남성": "ko-KR-InJoonNeural",
          "쇼츠여성": "openai:coral", "또렷여성": "openai:shimmer", "OpenAI여성": "openai:nova", "OpenAI남성": "openai:onyx",
          "자연여성": "openai:marin", "자연남성": "openai:cedar"}
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_STYLES = {  # 목소리별 말투 지시 (없으면 OPENAI_TTS_STYLE)
    "shimmer": "20대 한국 여성 유튜버처럼 밝고 친근하게, 또박또박 빠르게 말해 주세요. 문장 사이를 길게 쉬지 마세요.",
}
# 자연여성·자연남성(marin·cedar): 사용자가 고른 요리 채널 내레이터 말투 (2026-10-12 비교에서 가장 또렷했음)
OPENAI_TTS_STYLES["marin"] = OPENAI_TTS_STYLES["cedar"] = (
    "요리 잘하는 친절한 언니가 시청자에게 꿀팁 알려 주듯 밝고 자연스러운 존댓말로 말해 주세요. 읽는 티가 나면 안 되고, "
    "놀라거나 감탄하는 감정은 진짜처럼 살려서. 빠른 템포로, 문장 사이를 쉬지 말고 바로 이어서.")
OPENAI_TTS_STYLE = ("한국 유튜브 쇼츠에서 흔히 들리는 AI 성우 톤으로 말해 주세요. 20대 여성, 하이톤, 아주 밝고 경쾌하게, "
                    "빠른 속도로, 끝을 살짝 올리며 리듬감 있게. 아나운서처럼 딱딱하면 안 되고 문장 사이를 길게 쉬지 마세요.")
DEFAULT_KEYWORDS = ("촉촉", "바삭", "탱글", "쫀득")
MAX_CHARS = 12        # 자막 한 줄 최대 글자 수
MAX_SHOT = 1.5        # 한 화면이 이보다 길면 같은 클립의 다른 부분으로 한 번 더 끊는다
MOVES = ("in", "out", "pan_left", "pan_right")  # 컷마다 돌아가며 쓰는 카메라 움직임
MIN_SHOT = 1.0
GAP = 0.08            # 줄 사이 숨 고르기 (초). 길면 말이 늘어져 보인다
ZOOM = 0.06           # 컷마다 천천히 다가가는 정도 (6%)
PUNCH = 0.18          # 키워드 컷에서 확 당기는 정도 (18%, 0.3초 동안)
SUB_CY = 0.60         # 자막 중심 높이 (화면 높이 비율, 가운데보다 살짝 아래)
YELLOW = (255, 214, 10, 255)
FONTS = FONT_CANDIDATES + ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                           "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"]


# ---------------------------------------------------------------- 대본

def parse_script(text, keyword=None):
    """대본 글 → (키워드, [{"clip": 파일이름 또는 None, "text": 자막}])."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(키워드|keyword)\s*[:：]\s*(.+)$", line, re.I)
        if m:
            keyword = keyword or m[2].strip().strip("'\"‘’“”")
            continue
        clip = None
        if "|" in line:
            left, right = (s.strip() for s in line.split("|", 1))
            names = [n.strip() for n in left.split(",") if n.strip()]
            if names and all(Path(n).suffix.lower() in VIDEO_EXTS for n in names):
                clip, line = names, right
        line = re.sub(r"^\d+\s*[.)]\s*", "", line)  # "1. " 같은 번호는 뺀다
        hook = line.startswith("!")
        nostep = line.startswith("-")  # '-'로 시작하면 교육용 템플릿에서 STEP 번호를 안 붙인다 (결과·리액션 줄)
        line = line.lstrip("!-").strip()
        card = None
        m = re.search(r"\[\s*카드\s*[:：]\s*(.+?)\]", line)
        if m:
            title, _, sub = m[1].partition("/")
            card = (title.strip(), sub.strip())
            line = (line[:m.start()] + line[m.end():]).strip()
        if line:
            lines.append({"clip": clip, "text": line, "hook": hook, "card": card, "nostep": nostep})
    if not lines:
        raise UserError("대본이 비어 있습니다. 한 줄에 한 컷씩 자막을 적어 주세요.")
    if not keyword:  # 키워드를 안 적었으면 대본에 나온 식감 단어를 쓴다
        joined = " ".join(l["text"] for l in lines)
        keyword = next((k for k in DEFAULT_KEYWORDS if k in joined), "")
    return keyword, lines


def wrap(text, max_chars=MAX_CHARS):
    """한 줄 max_chars 이내로 나눈다. "?" "!" "." 뒤에서는 꼭 줄을 바꾸고, 한 줄에 안 들어가면 첫 쉼표 뒤에서 끊는다.
    두 줄이 되면 위아래 길이가 비슷하게 나눈다. 너무 긴 단어는 글자 단위로 자른다."""
    sentences = [x for x in re.split(r"(?<=[?!.])\s+", text.strip()) if x]
    if len(sentences) == 1 and len(text) > max_chars:
        sentences = [x for x in re.split(r"(?<=,)\s+", text.strip(), maxsplit=1) if x]
    return [row for x in sentences for row in _wrap_words(x, max_chars)]


def _wrap_words(text, max_chars):
    out, cur = [], ""
    for word in text.split():
        while len(word) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    if len(out) == 2:  # 두 줄이면 위아래 균형 있게 ("팜유가 주원료면" / "치즈가 아니에요")
        words = text.split()
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        ok = [sp for sp in splits if max(map(len, sp)) <= max_chars]
        if ok:
            out = list(min(ok, key=lambda sp: max(map(len, sp))))
    return out


def subtitle_chunks(text, max_chars=MAX_CHARS, per=2):
    """자막을 화면에 한 번에 보일 덩어리(최대 per줄)로 나눈다."""
    rows = wrap(text, max_chars)
    return [rows[i:i + per] for i in range(0, len(rows), per)]


# ---------------------------------------------------------------- 자막 그림

def _font(size):
    path = next((f for f in FONTS if Path(f).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def _runs(row, keyword):
    """한 줄을 (글자, 키워드인지) 조각으로."""
    if not keyword or keyword not in row:
        return [(row, False)]
    parts = row.split(keyword)
    runs = []
    for i, p in enumerate(parts):
        if p:
            runs.append((p, False))
        if i < len(parts) - 1:
            runs.append((keyword, True))
    return runs


def subtitle_sprite(rows, keyword, scale=1.0):
    """흰 글자 + 검은 테두리 + 부드러운 그림자, 키워드는 노란색. 키워드가 있는 자막은 조금 더 크게."""
    big = bool(keyword) and any(keyword in r for r in rows)
    fs = int(W * (0.082 if big else 0.07) * scale)
    font = _font(fs)
    sw = max(3, int(fs * 0.12))
    pad = int(fs * 0.5)
    line_h = int(fs * 1.3)
    widths = [int(font.getlength(r)) for r in rows]
    w, h = max(widths) + pad * 2 + sw * 2, line_h * len(rows) + pad * 2
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, row in enumerate(rows):
        x = (w - widths[i]) / 2
        y = pad + i * line_h
        ds.text((x, y + fs * 0.07), row, font=font, fill=(0, 0, 0, 150), stroke_width=sw + 2, stroke_fill=(0, 0, 0, 150))
        for part, is_kw in _runs(row, keyword):
            d.text((x, y), part, font=font, fill=YELLOW if is_kw else (255, 255, 255, 255),
                   stroke_width=sw, stroke_fill=(15, 15, 15, 255))
            x += font.getlength(part)
    return Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(fs * 0.08)), img)


def card_sprite(title, sub):
    """상품 카드: 흰 둥근 상자에 상품 이름(검정) + 한 줄 설명(빨강, 크게)."""
    fs_t, fs_s = int(W * 0.06), int(W * 0.085)
    pad = int(W * 0.04)
    widest = max(_font(fs_t).getlength(title), _font(fs_s).getlength(sub) if sub else 0)
    if widest > W * 0.9 - pad * 2:  # 글이 길면 화면 폭에 맞게 줄인다
        k = (W * 0.9 - pad * 2) / widest
        fs_t, fs_s = int(fs_t * k), int(fs_s * k)
    ft, fsub = _font(fs_t), _font(fs_s)
    w = int(max(ft.getlength(title), fsub.getlength(sub) if sub else 0) + pad * 2)
    h = int(pad * 1.6 + fs_t * 1.25 + (fs_s * 1.25 if sub else 0))
    img = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((8, 14, w + 8, h + 14), radius=pad, fill=(0, 0, 0, 120))
    img = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)), img)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, w + 8, h + 8), radius=pad, fill=(255, 255, 255, 245))
    y = 8 + pad * 0.8
    d.text((8 + (w - ft.getlength(title)) / 2, y), title, font=ft, fill=(25, 25, 25, 255))
    if sub:
        d.text((8 + (w - fsub.getlength(sub)) / 2, y + fs_t * 1.25), sub, font=fsub, fill=(225, 30, 40, 255))
    return img


def title_sprite(title):
    """교육용 템플릿의 위쪽 제목 띠: 화면 폭 반투명 검정 띠 + 왼쪽 노란 막대 + 흰 굵은 제목."""
    fs = int(W * 0.062)
    if _font(fs).getlength(title) > W * 0.84:
        fs = int(fs * W * 0.84 / _font(fs).getlength(title))
    f = _font(fs)
    h = int(fs * 1.9)
    img = Image.new("RGBA", (W, h), (12, 12, 14, 200))
    d = ImageDraw.Draw(img)
    bar = int(W * 0.018)
    d.rectangle((int(W * 0.05), int(h * 0.22), int(W * 0.05) + bar, int(h * 0.78)), fill=YELLOW)
    d.text((int(W * 0.05) + bar * 2, (h - fs * 1.18) / 2), title, font=f, fill=(255, 255, 255, 255))
    return img


CUTE_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Jua-Regular.ttf"  # 동글동글 귀여운 글꼴 (OFL)
CREAM, RED, INK = (255, 248, 230, 255), (232, 52, 62, 255), (22, 22, 22, 255)


def _cute(size):
    return ImageFont.truetype(str(CUTE_FONT), size) if CUTE_FONT.exists() else _font(size)


def sticker_sprite(title):
    """스티커 제목 (사용자가 좋아한 김밥 쇼츠 스타일): 크림색 둥근 바탕 + 흰 테두리, 귀여운 글꼴,
    앞부분은 검정·마지막 단어는 빨강, 양옆에 반짝 선과 하트."""
    words = title.split()
    head, tail = (" ".join(words[:-1]) + " ", words[-1]) if len(words) > 1 else ("", title)
    fs = int(W * 0.105)
    while _cute(fs).getlength(head + tail) > W * 0.72 and fs > 40:
        fs -= 4
    f = _cute(fs)
    sw = max(4, fs // 9)
    tw = f.getlength(head + tail)
    bw, bh = int(tw + fs * 0.9), int(fs * 1.45)
    w, h = W, bh + 40
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x0, y0 = (w - bw) // 2, 20
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 10, x0 + bw, y0 + bh + 10), radius=bh // 2, fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=bh // 2, fill=CREAM, outline=(255, 255, 255, 255),
                        width=sw)
    tx, ty = (w - tw) / 2, y0 + (bh - fs * 1.12) / 2
    d.text((tx, ty), head, font=f, fill=INK, stroke_width=max(2, sw // 2), stroke_fill=(255, 255, 255, 255))
    d.text((tx + f.getlength(head), ty), tail, font=f, fill=RED, stroke_width=max(2, sw // 2),
           stroke_fill=(255, 255, 255, 255))
    # 반짝 선 (양옆 세 줄씩)
    lw = max(5, fs // 12)
    for side in (-1, 1):
        cx = x0 - fs * 0.25 if side < 0 else x0 + bw + fs * 0.25
        cy = y0 + bh / 2
        for ang in (-35, 0, 35):
            v = np.array([np.cos(np.radians(ang)), np.sin(np.radians(ang))]) * side
            p1, p2 = (cx + v[0] * fs * 0.05, cy + v[1] * fs * 0.4), (cx + v[0] * fs * 0.45, cy + v[1] * fs * 0.62)
            d.line((p1, p2), fill=(255, 255, 255, 255), width=lw + 6)
            d.line((p1, p2), fill=INK, width=lw)
    # 왼쪽 위 작은 하트
    r = fs * 0.2
    hx, hy = x0 + fs * 0.15, y0 - r * 0.2
    d.ellipse((hx - r, hy - r * 0.6, hx, hy + r * 0.4), fill=RED)
    d.ellipse((hx, hy - r * 0.6, hx + r, hy + r * 0.4), fill=RED)
    d.polygon([(hx - r * 0.95, hy), (hx + r * 0.95, hy), (hx, hy + r * 1.1)], fill=RED)
    return img


def _thumb(clip, size):
    """순서 띠의 동그란 사진: 클립과 같은 이름의 .png가 있으면 그걸, 없으면 클립 첫 화면."""
    clip = Path(clip)
    src = clip.with_suffix(".png")
    if not src.exists():
        src = clip.with_suffix(".thumb.jpg")
        if not src.exists():
            run(["-ss", "0.3", "-i", str(clip), "-frames:v", "1", str(src)])
    im = Image.open(src).convert("RGB")
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
    im = im.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def order_sprite(steps, current=None):
    """조리 순서 띠: 동그란 사진 + 이름을 화살표로 이어서 보여 준다. 지금 단계는 빨간 테두리로 크게."""
    n = len(steps)
    cell = W * 0.94 / n
    size = int(min(cell * 0.78, W * 0.17))
    fs = int(min(W * 0.042, cell * 0.24))
    f = _cute(fs)
    h = int(size * 1.15 + fs * 1.6 + 16)
    img = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (clip, label) in enumerate(steps):
        cx = W * 0.03 + cell * (i + 0.5)
        big = i == current
        s = int(size * (1.12 if big else 0.92))
        y = 8 + (size * 1.12 - s) / 2
        ring = max(5, s // 18)
        d.ellipse((cx - s / 2 - ring, y - ring, cx + s / 2 + ring, y + s + ring),
                  fill=RED if big else (255, 255, 255, 255))
        img.alpha_composite(_thumb(clip, s), (int(cx - s / 2), int(y)))
        lw = f.getlength(label)
        ly = 8 + size * 1.12 + fs * 0.25
        pad = fs * 0.35
        d.rounded_rectangle((cx - lw / 2 - pad, ly - pad * 0.4, cx + lw / 2 + pad, ly + fs * 1.15 + pad * 0.2),
                            radius=fs // 2, fill=RED if big else CREAM)
        d.text((cx - lw / 2, ly), label, font=f, fill=(255, 255, 255, 255) if big else INK)
        if i < n - 1:  # 다음 단계로 가는 화살표
            ax, ay, al = cx + cell * 0.5, 8 + size * 0.56, cell * 0.11
            d.line((ax - al, ay, ax + al * 0.6, ay), fill=(255, 255, 255, 255), width=12)
            d.polygon([(ax + al, ay), (ax + al * 0.2, ay - al * 0.6), (ax + al * 0.2, ay + al * 0.6)],
                      fill=(255, 255, 255, 255), outline=INK)
            d.line((ax - al, ay, ax + al * 0.6, ay), fill=INK, width=6)
    return img


def step_sprite(n):
    """교육용 템플릿의 단계 표시: 노란 알약 모양에 'STEP n'."""
    fs = int(W * 0.042)
    f = _font(fs)
    text = f"STEP {n}"
    w, h = int(f.getlength(text) + fs * 1.4), int(fs * 1.7)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=YELLOW)
    d.text(((w - f.getlength(text)) / 2, (h - fs * 1.18) / 2), text, font=f, fill=(20, 20, 20, 255))
    return img


def badge_sprite(text):
    """왼쪽 위 작은 배지 (예: '30초 레시피'): 흰 굵은 글자 두 줄, 검은 그림자."""
    fs = int(W * 0.05)
    f = _font(fs)
    rows = text.split(" ", 1) if len(text) > 4 else [text]
    w = int(max(f.getlength(r) for r in rows) + fs)
    h = int(fs * 1.25 * len(rows) + fs * 0.6)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, r in enumerate(rows):
        d.text((fs * 0.5, fs * 0.3 + i * fs * 1.25), r, font=f, fill=(255, 255, 255, 255),
               stroke_width=max(2, fs // 12), stroke_fill=(0, 0, 0, 255))
    return img


def count_sprite(n):
    """카운트다운 큰 숫자: 흰 글자 + 검은 테두리."""
    fs = int(W * 0.13)
    f = _font(fs)
    text = str(n)
    sw = max(4, fs // 14)
    w, h = int(f.getlength(text) + sw * 4), int(fs * 1.3)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((sw * 2, 0), text, font=f, fill=(255, 255, 255, 255), stroke_width=sw,
                             stroke_fill=(0, 0, 0, 255))
    return img


# ---------------------------------------------------------------- 목소리

def _openai_tts(text, voice, rate, out_path):
    """OpenAI 목소리 (OPENAI_API_KEY 필요). 말투는 OPENAI_TTS_STYLE로 지시, 빠르기는 atempo로 맞춘다."""
    from .env import load_env
    load_env(Path(__file__).resolve().parent.parent)
    if not os.environ.get("OPENAI_API_KEY"):
        raise UserError("OpenAI 목소리를 쓰려면 .env에 OPENAI_API_KEY가 필요합니다.")
    import openai
    client = openai.OpenAI(max_retries=4)
    raw = Path(out_path).with_suffix(".raw.mp3")
    try:
        with client.audio.speech.with_streaming_response.create(
                model=OPENAI_TTS_MODEL, voice=voice, input=text, instructions=OPENAI_TTS_STYLES.get(voice, OPENAI_TTS_STYLE),
                response_format="mp3") as r:
            r.stream_to_file(raw)
    except openai.OpenAIError as e:
        raise UserError(f"OpenAI 목소리를 만들지 못했습니다: {type(e).__name__}") from None
    m = re.fullmatch(r"\s*([+-]\d+)%\s*", rate or "")
    tempo = min(2.0, max(0.5, 1 + int(m.group(1)) / 100)) if m else 1.0
    run(["-i", raw, "-filter:a", f"atempo={tempo:.3f}", out_path])
    raw.unlink(missing_ok=True)


def tts(text, voice, rate, out_path):
    """한국어 AI 목소리 mp3를 만든다. 기본은 edge-tts(무료), 'openai:목소리'면 OpenAI."""
    name = VOICES.get(voice, voice)
    if name.startswith("openai:"):
        return _openai_tts(text, name.split(":", 1)[1], rate, out_path)
    try:
        import edge_tts
    except ImportError:
        raise UserError("AI 목소리 모듈이 없습니다. 명령창에서 'pip install edge-tts'를 실행해 주세요.") from None
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    for attempt in range(4):
        try:  # Communicate는 한 번만 쓸 수 있어서 시도마다 새로 만든다
            comm = edge_tts.Communicate(text, name, rate=rate, proxy=proxy)
            asyncio.run(comm.save(str(out_path)))
        except Exception as e:  # 일시적인 연결 오류는 다시 시도
            if attempt == 3:
                raise UserError(f"AI 목소리를 만들지 못했습니다 (인터넷 연결 확인): {type(e).__name__}") from None
            continue
        # 가끔 말이 중간에 끊긴 파일이 온다 → 글자 수에 비해 너무 짧으면 다시 받는다
        n_chars = len(re.sub(r"[\s\W]", "", text))
        if len(decode(out_path)) / SR >= 0.07 * n_chars or attempt == 3:
            return


def decode(path, start=0.0, dur=None):
    """오디오를 48kHz 스테레오 float32(-1~1)로. 오디오가 없으면 무음."""
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(path)]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    raw = subprocess.run(args + ["-vn", "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32) / 32768
    if dur:
        n = int(dur * SR)
        a = np.vstack([a, np.zeros((max(0, n - len(a)), 2), np.float32)])[:n]
    return a


def trim_silence(a, db=-42.0, keep=0.03, max_pause=0.18):
    """AI 목소리 앞뒤 무음을 잘라내고, 문장 사이 긴 쉼("?" "!" 뒤 0.5초 넘게)은 max_pause초로 줄인다."""
    if not len(a):
        return a
    loud = np.abs(a).max(axis=1) > 10 ** (db / 20)
    idx = np.flatnonzero(loud)
    if not len(idx):
        return a
    k = int(keep * SR)
    a = a[max(0, idx[0] - k):idx[-1] + k]
    idx = np.flatnonzero(np.abs(a).max(axis=1) > 10 ** (db / 20))
    gaps = np.flatnonzero(np.diff(idx) > max_pause * SR)
    if not len(gaps):
        return a
    parts, prev = [], 0
    half = int(max_pause * SR / 2)
    for g in gaps:  # 쉼의 앞쪽 절반, 뒤쪽 절반만 남긴다
        parts.append(a[prev:idx[g] + half])
        prev = idx[g + 1] - half
    parts.append(a[prev:])
    return np.vstack(parts)


def fade(a, sec=0.03):
    n = min(int(sec * SR), len(a) // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        a[:n] *= ramp
        a[-n:] *= ramp[::-1]
    return a


def add_sfx(mix, kind, at, vol):
    s = _sfx(kind)
    a = max(0, int(at * SR))
    b = min(len(mix), a + len(s))
    if b > a:
        mix[a:b] += s[:b - a, None].astype(np.float32) * vol


# ---------------------------------------------------------------- 만들기

def list_clips(folder):
    return sorted((p for p in Path(folder).iterdir() if p.suffix.lower() in VIDEO_EXTS),
                  key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.stem)])


def assign_clips(lines, clips):
    """대본 줄마다 클립을 붙인다. 이름을 적은 줄은 그 클립, 나머지는 남은 클립을 순서대로."""
    by_name = {c.name: c for c in clips}
    named = {n for l in lines if l["clip"] for n in l["clip"]}
    missing = [n for n in named if n not in by_name]
    if missing:
        raise UserError(f"대본에 적은 클립이 폴더에 없습니다: {', '.join(missing)}")
    rest = iter(c for c in clips if c.name not in named)
    out = []
    for l in lines:
        clip = [by_name[n] for n in l["clip"]] if l["clip"] else next(rest, None)
        if clip is None:
            need = len(lines) - len(named)
            raise UserError(f"클립이 부족합니다. 대본은 {len(lines)}줄인데 클립이 {len(clips)}개예요 "
                            f"(이름을 안 적은 줄 {need}개에 클립이 하나씩 필요).")
        out.append(clip if isinstance(clip, list) else [clip])
    return out  # 줄마다 클립 목록


def make_short(clips, script, out, work, keyword=None, voice="여성", rate="+10%", bgm=None,
               clip_volume=0.35, bgm_volume=0.12, voice_volume=1.0, gap=GAP):
    """gap: 줄 사이 쉼(초). 0이면 다큐 내레이션처럼 쉬지 않고 이어 말한다."""
    """클립 목록 + 대본 글 → 완성본 mp4 (+ 같은 이름의 .srt). voice="없음"이면 AI 목소리 없이 클립 소리만."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    # '제목:' 줄이 있으면 교육용 템플릿: 위쪽 제목 띠 + 과정 줄마다 STEP 번호
    def header(name):
        nonlocal script
        m = re.search(rf"^\s*{name}\s*[:：]\s*(.+?)\s*$", script, re.M)
        if not m:
            return None
        script = script[:m.start()] + script[m.end():]
        return m[1]
    title = header("제목")
    badge = header("배지")  # 왼쪽 위 배지 (예: 30초 레시피)
    countdown = (header("카운트다운") or "").strip() in ("켬", "on", "예", "1")
    max_shot = float(header("컷길이") or MAX_SHOT)
    loop = (header("루프") or "").strip() in ("켬", "on", "예", "1")
    natural = (header("자연") or "").strip() in ("켬", "on", "예", "1")
    sticker = (header("스티커") or "").strip() in ("켬", "on", "예", "1")
    order = header("순서")  # "a.mp4=씻기, b.mp4=볶기, …" → 과정 줄마다 위쪽에 조리 순서 띠
    keyword, lines = parse_script(script, keyword)
    step = 0
    for l in lines:
        l["step"] = None
        if title and not (l["hook"] or l["card"] or l["nostep"]):
            step += 1
            l["step"] = step
    clips = assign_clips(lines, [Path(c) for c in clips])
    order_steps = []
    if order:
        by_name = {Path(c).name: Path(c) for clist in clips for c in clist}
        for item in order.split(","):
            name, _, label = item.partition("=")
            name = name.strip()
            if name not in by_name:
                raise UserError(f"'순서:'에 적은 클립 '{name}'이 대본에 없어요.")
            order_steps.append((by_name[name], label.strip() or Path(name).stem))
    print(f"대본 {len(lines)}줄 · 키워드 '{keyword or '없음'}' · 목소리 {voice}")

    # 1) 줄마다 목소리를 만들고 길이를 정한다
    speech, durs = [], []
    for i, l in enumerate(lines):
        jobctl.check()
        if voice != "없음":
            mp3 = work / f"voice_{i:02d}.mp3"
            tts(l["text"], voice, rate, mp3)
            a = trim_silence(decode(mp3), max_pause=min(0.18, max(0.08, gap)))
            dur = max(MIN_SHOT if gap > 0 else 0.5, len(a) / SR + gap)
        else:
            a = np.zeros((0, 2), np.float32)
            dur = max(1.2, len(l["text"]) * 0.17 + 0.4)  # 읽기 좋은 시간
        speech.append(a)
        durs.append(round(dur * FPS) / FPS)  # 프레임 단위로 맞춰 오디오와 영상이 어긋나지 않게
        print(f"  {i + 1:2d}. {dur:4.1f}초  {l['text']}")

    # 카운트다운: 후킹 줄을 뺀 줄마다 남은 개수 (마지막 줄이 1)
    left = sum(1 for l in lines if not l["hook"])
    for l in lines:
        l["count"] = None
        if countdown and not l["hook"]:
            l["count"] = left
            left -= 1

    # 2) 화면 조각: 줄이 max_shot보다 길면 끊는다. 클립이 여럿이면 클립마다, 하나면 같은 클립의 앞 → 뒤
    shots, cues, t = [], [], 0.0
    for i, (l, clist, dur) in enumerate(zip(lines, clips, durs)):
        n = max(len(clist), int(np.ceil(dur / max_shot - 0.15)))
        n = max(1, min(n, int(dur / 0.4)))  # 한 조각이 0.4초보다 짧아지지는 않게
        part = round(dur / n * FPS) / FPS
        for k in range(n):
            d = part if k < n - 1 else round((dur - part * (n - 1)) * FPS) / FPS
            clip = clist[k % len(clist)]
            clen = probe(clip)["duration"]
            # 같은 클립을 다시 쓰는 조각은 클립 뒤쪽에서 → 같은 클립이라도 화면이 바뀐다
            again = k >= len(clist)
            off = 0.2 if not again else max(0.2, clen - d - 0.2)
            off = min(off, max(0.0, clen - d))
            shots.append({"clip": clip, "start": off, "dur": d, "line": i})
        # 후킹 줄은 글자가 커서 한 줄 10자, 한 화면에 3줄까지
        chunks = subtitle_chunks(l["text"], 10, 3) if l["hook"] else subtitle_chunks(l["text"])
        total = sum(len("".join(c)) for c in chunks)
        ct = t
        for c in chunks:
            cd = dur * len("".join(c)) / total
            cues.append({"start": round(ct, 2), "end": round(ct + cd - 0.03, 2), "rows": c, "text": " ".join(c),
                         "line": i})
            ct += cd
        t += dur
    total_dur = t
    if loop and len(shots) > 1:
        # 루프: 마지막 조각은 첫 조각과 같은 장면 → 끝 프레임이 첫 프레임과 똑같아 다시 재생될 때 이어 보인다
        shots[-1]["clip"], shots[-1]["start"] = shots[0]["clip"], shots[0]["start"]

    # 3) 화면 조각마다 세로로 맞추고 자막을 얹어 인코딩
    seg_files = []
    for j, s in enumerate(shots):
        jobctl.check()
        shot_start = sum(x["dur"] for x in shots[:j])
        overlays = []
        line = lines[s["line"]]
        last = loop and j == len(shots) - 1 and j > 0
        if title:  # 교육용 템플릿: 제목 띠(또는 스티커 제목)는 영상 내내 맨 위에
            png = work / "title.png"
            (sticker_sprite if sticker else title_sprite)(title).save(png)
            overlays.append((png, 0, s["dur"], H * (0.105 if sticker else 0.10)))
        if order_steps and line["step"]:
            # 과정 줄에서 지금까지 나온 가장 뒤 단계를 빨갛게 (후킹 줄 화면은 세지 않고, 뒤로 돌아가지 않게)
            names = [c.name for c, _ in order_steps]
            seen = [x["clip"].name for x in shots[:j + 1] if lines[x["line"]]["step"]]
            cur = max((names.index(n) for n in seen if n in names), default=None)
            png = work / f"order_{cur}.png"
            order_sprite(order_steps, cur).save(png)
            overlays.append((png, 0, s["dur"], H * 0.225))
        if badge:
            png = work / "badge.png"
            badge_sprite(badge).save(png)
            overlays.append((png, 0, s["dur"], H * (0.17 if title else 0.08), W * 0.04))
        if line["count"]:
            png = work / f"count_{line['count']:02d}.png"
            count_sprite(line["count"]).save(png)
            overlays.append((png, 0, s["dur"], H * (0.19 if title else 0.09), -W * 0.05))
        if line["step"]:
            png = work / f"step_{line['step']:02d}.png"
            step_sprite(line["step"]).save(png)
            overlays.append((png, 0, s["dur"], H * (SUB_CY - 0.075)))
        if line["card"]:  # 상품 카드는 그 줄 내내 화면 위쪽에 (제목 띠가 있으면 그 아래)
            png = work / f"card_{s['line']:02d}.png"
            card_sprite(*line["card"]).save(png)
            # 루프의 마지막 조각에서는 끝 0.5초 전에 카드를 내려 첫 화면과 똑같이 끝나게
            overlays.append((png, 0, s["dur"] - 0.5 if last and s["dur"] > 1 else s["dur"],
                             H * (0.24 if title else 0.17)))
        for k, c in enumerate(cues):
            a, b = max(c["start"], shot_start), min(c["end"], shot_start + s["dur"])
            if b > a and c["line"] == s["line"]:
                png = work / f"sub_{k:02d}.png"
                # 대본을 고쳐 다시 돌릴 수 있으니 항상 새로 그림. 후킹 줄은 크게, 위쪽에
                subtitle_sprite(c["rows"], keyword, 1.4 if line["hook"] else 1.0).save(png)
                overlays.append((png, a - shot_start, b - shot_start, H * (0.30 if line["hook"] else SUB_CY)))
        seg = work / f"shot_{j:02d}.mp4"
        inputs = ["-ss", f"{s['start']:.3f}", "-i", str(s["clip"])]
        n = max(1, round(s["dur"] * FPS))
        if keyword and keyword in lines[s["line"]]["text"] and s is next(x for x in shots if x["line"] == s["line"]):
            pn = round(0.3 * FPS)  # 키워드 컷: 0.3초 만에 확 당기고 그대로 유지
            z = f"1+{PUNCH}*min(on/{pn},1)*(2-min(on/{pn},1))"
        else:
            z = None
        # 단조롭지 않게: 컷마다 카메라 움직임을 돌아가며 바꾼다 (다가가기 / 물러나기 / 왼쪽·오른쪽 흐르기)
        cx, cy_ = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
        move = j % len(MOVES) if z is None else -1
        if natural and z is None:
            # 손으로 든 듯한 흔들림: 살짝 확대한 채 천천히 다가가며 좌우·위아래로 조금씩 흔들린다
            z = f"1.10+0.03*on/{n}"
            cx = f"iw/2-iw/zoom/2+iw*0.012*sin(on/{FPS}*2.1+{j})"
            cy_ = f"ih/2-ih/zoom/2+ih*0.008*cos(on/{FPS}*1.7+{j * 2})"
            move = -1
        if move == 1:
            z = f"1+{ZOOM * 2}*(1-on/{n})"
        elif move in (2, 3):
            z = "1.12"
            sweep = f"(iw-iw/zoom)*on/{n}"
            cx = sweep if move == 2 else f"(iw-iw/zoom)-{sweep}"
        elif z is None:
            z = f"1+{ZOOM}*on/{n}"
        if last:
            # 첫 조각의 카메라 움직임을 거꾸로 돌려 마지막 프레임이 첫 프레임 위치에 딱 멈추게
            r = f"(on-{n - 1})"
            if natural:
                z = f"1.10+0.03*{r}/{n}"
                cx = f"iw/2-iw/zoom/2+iw*0.012*sin({r}/{FPS}*2.1)"
                cy_ = f"ih/2-ih/zoom/2+ih*0.008*cos({r}/{FPS}*1.7)"
            else:
                cx, cy_ = "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"
                z = f"1-{ZOOM}*{r}/{n}"
        # 새 줄이 시작되는 컷은 하얗게 번쩍 (0.08초)
        flash = ",fade=t=in:st=0:d=0.08:color=white" if s is next(x for x in shots if x["line"] == s["line"]) and j else ""
        if natural:
            flash = ""
        fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration={s['dur']:.3f},"
              f"zoompan=z='{z}':x='{cx}':y='{cy_}':d=1:s={W}x{H}:fps={FPS}{flash}[v0]")
        for k, (png, a, b, cy, *xs) in enumerate(overlays):
            inputs += ["-i", str(png)]
            # 자막이 0.1초 동안 아래에서 톡 튀어 오르듯 나타난다
            pop = f"{H * 0.025:.0f}*max(0,1-(t-{a:.3f})/0.1)" if png.name.startswith("sub_") else "0"
            # x: 없으면 가운데, 양수면 왼쪽에서 그만큼, 음수면 오른쪽에서 그만큼
            x = "(W-w)/2" if not xs else (f"{xs[0]:.0f}" if xs[0] >= 0 else f"W-w-{-xs[0]:.0f}")
            fc += (f";[v{k}][{k + 1}:v]overlay=x={x}:y='{cy:.0f}-h/2+{pop}':"
                   f"enable='between(t,{a:.3f},{b:.3f})'[v{k + 1}]")
        run([*inputs, "-filter_complex", fc, "-map", f"[v{len(overlays)}]", "-t", f"{s['dur']:.3f}", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), seg])
        seg_files.append(seg)
        print(f"\r화면 조각 {j + 1}/{len(shots)}", end="")
    print()

    # 4) 소리: 클립 실제 소리(작게) + AI 목소리 + 배경음악
    n_total = int(round(total_dur * SR))
    mix = np.zeros((n_total, 2), np.float32)
    pos = 0
    for s in shots:
        n = int(round(s["dur"] * SR))
        mix[pos:pos + n] += fade(decode(s["clip"], s["start"], s["dur"]))[:n_total - pos] * clip_volume
        pos += n
    pos = 0
    for a, dur in zip(speech, durs):
        n = min(len(a), n_total - pos)
        mix[pos:pos + n] += a[:n] * voice_volume
        pos += int(round(dur * SR))
    # 효과음: 키워드 자막이 뜰 때만 '뿅'. 컷마다 '휙'은 거슬린다는 피드백으로 뺐다
    for c in cues:
        if keyword and keyword in c["text"]:
            add_sfx(mix, "pop", c["start"], 0.6)
    if bgm:
        music = decode(bgm)
        if len(music):
            reps = int(np.ceil(n_total / len(music)))
            k = int(0.5 * SR)  # 루프: 끝 다음에 올 0.5초를 처음에 겹쳐 이음매를 없앤다
            music = np.tile(music, (reps + 1, 1))[:n_total + k]
            if loop and len(music) > n_total + k - 1 and n_total > 2 * k:
                ramp = np.linspace(0, 1, k, dtype=np.float32)[:, None]
                head = music[:k] * ramp + music[n_total:n_total + k] * (1 - ramp)
                music = music[:n_total].copy()
                music[:k] = head
            else:
                music = music[:n_total].copy()
                n = min(len(music), int(1.0 * SR))  # 끝 1초는 서서히 줄인다
                music[-n:] *= np.linspace(1, 0, n, dtype=np.float32)[:, None]
            mix += music * bgm_volume
    wav = work / "mix.wav"
    write_wav(wav, np.clip(mix * 32767, -32768, 32767).astype(np.int16))

    # 5) 이어 붙이고 소리를 입혀 완성
    listing = work / "shots.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-f", "concat", "-safe", "0", "-i", listing, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", str(SR), "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-shortest", out])
    write_srt([{"start": c["start"], "end": c["end"], "text": c["text"]} for c in cues], out.with_suffix(".srt"))
    print(f"완성: {out}  ({total_dur:.1f}초, 화면 {len(shots)}개)")
    return out
