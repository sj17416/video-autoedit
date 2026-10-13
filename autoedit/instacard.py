"""인스타그램 게시물 카드 합성 (내 PC에서 PIL로 → 장당 비용 0원).

레이아웃: 위쪽은 사진, 아래로 갈수록 검게 번지는 그라데이션, 그 위에 큰 제목. 맨 위에 브랜드 로고(또는 이름).
제목의 [대괄호] 부분은 강조색으로 칠한다. 예: "이집트에서 [절대 하면 안 되는] 3가지"
"""
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

RATIOS = {"1:1": (1080, 1080), "4:5": (1080, 1350)}
LANGS = {"ko": "한국어", "ja": "日本語", "en": "English", "fr": "Français", "es": "Español", "pt": "Português",
         "de": "Deutsch"}
HIGHLIGHT = (255, 216, 77)   # 강조 (노랑)
TEXT = (255, 255, 255)

CARD_DEFAULTS = {
    "font_scale": 1.0,     # 제목 글자 크기 배율
    "gradient": 0.75,      # 그라데이션 진하기 (0~1)
    "photo_ratio": 0.62,   # 사진이 차지하는 높이 비율 (나머지는 제목 영역)
    "upper": True,         # 영어·유럽어 제목을 대문자로
    "enhance": True,       # 사진 자동 보정 (대비·채도·선명도 살짝)
    "logo_pos": "center",  # left / center / right
}

# ---------------------------------------------------------------- 폰트 (Windows 기본 폰트 → 리눅스 테스트용 Noto)
FONT_DIRS = [Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
             Path(__file__).resolve().parent.parent / "assets" / "fonts",
             Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/truetype/dejavu")]
FONT_CANDIDATES = {
    "ko": [("malgunbd.ttf", 0), ("NotoSansKR-Bold.ttf", 0), ("NotoSansCJK-Bold.ttc", 1)],
    "ja": [("YuGothB.ttc", 0), ("meiryob.ttc", 0), ("NotoSansJP-Bold.ttf", 0), ("NotoSansCJK-Bold.ttc", 0),
           ("malgunbd.ttf", 0)],
    # Impact: 좁고 굵은 영문 제목 폰트 (예시 화면과 같은 느낌). 악센트(é, ñ, ü, ã)도 지원
    "latin": [("impact.ttf", 0), ("arialbd.ttf", 0), ("DejaVuSans-Bold.ttf", 0)],
    "ui": [("malgunbd.ttf", 0), ("NotoSansCJK-Bold.ttc", 1), ("DejaVuSans-Bold.ttf", 0)],
}
_font_cache = {}


def _font_file(kind):
    if kind not in _font_cache:
        _font_cache[kind] = next(((d / name, idx) for name, idx in FONT_CANDIDATES[kind] for d in FONT_DIRS
                                  if (d / name).exists()), None)
    return _font_cache[kind]


def font_for(lang, size, kind=None):
    kind = kind or ("ko" if lang == "ko" else "ja" if lang == "ja" else "latin")
    found = _font_file(kind) or _font_file("ui")
    if not found:
        raise RuntimeError("제목에 쓸 폰트를 찾지 못했습니다. (C:/Windows/Fonts 에 맑은 고딕이 있어야 합니다)")
    return ImageFont.truetype(str(found[0]), size, index=found[1])


# ---------------------------------------------------------------- 사진 불러오기

def load_photo(data_or_path, max_side=2048):
    """EXIF 회전을 반영해 RGB로 연다. 너무 크면 줄인다."""
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, (bytes, bytearray)) else data_or_path
    img = ImageOps.exif_transpose(Image.open(src))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def dhash(img, size=8):
    """사진 지문 (64비트). 비슷한 사진은 비트 차이가 작다 → 중복 사진 걸러내기."""
    g = np.asarray(img.convert("L").resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hash_distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def sharpness(img):
    """선명도 점수 (라플라시안 분산). 영상에서 흔들린 장면을 거르는 데 쓴다."""
    g = np.asarray(img.convert("L").resize((320, int(320 * img.height / img.width) or 1)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def _focus_y(img):
    """세로로 자를 때 중심으로 삼을 높이(0~1). 얼굴이 있으면 얼굴, 없으면 살짝 위쪽."""
    try:
        import contextlib
        from .render import detect_faces
        bgr = np.asarray(img)[:, :, ::-1].copy()
        with contextlib.redirect_stdout(io.StringIO()):  # 영상용 안내 문구("오브젝트를 오른쪽에…")는 숨긴다
            faces = detect_faces(bgr, width=640)
    except Exception:
        faces = []
    if not faces:
        return 0.42
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return min(1.0, max(0.0, (y + h * 0.6) / img.height))


def _cover(img, W, H, focus_y=0.42):
    s = max(W / img.width, H / img.height)
    r = img.resize((max(W, round(img.width * s)), max(H, round(img.height * s))), Image.LANCZOS)
    x0 = (r.width - W) // 2
    y0 = int(min(max(focus_y * r.height - H / 2, 0), r.height - H))
    return r.crop((x0, y0, x0 + W, y0 + H))


def enhance(img):
    """원본보다 또렷하게: 자동 대비 + 채도·선명도 살짝."""
    img = ImageOps.autocontrast(img, cutoff=0.5, preserve_tone=True)
    img = ImageEnhance.Color(img).enhance(1.08)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))


# ---------------------------------------------------------------- 제목 줄바꿈

_NO_START = set("、。，．・：；？！ー）」』】〕〉》’”%,.!?:;)]}…ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")  # 줄 맨 앞에 오면 어색한 글자
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")


def parse_highlight(title):
    """'[강조]' 표시 → [(글자, 강조여부)] 조각 목록."""
    parts, hl = [], False
    for piece in re.split(r"([\[\]])", title):
        if piece == "[":
            hl = True
        elif piece == "]":
            hl = False
        elif piece:
            parts.append((piece, hl))
    return parts


def _tokens(parts, lang):
    """줄바꿈 단위로 쪼갠다: 일본어는 글자 단위, 나머지는 띄어쓰기 단위. [(글자, 강조, 앞에_공백)]"""
    out, space = [], False
    for text, hl in parts:
        for ch_or_word in (text if lang == "ja" else re.split(r"(\s+)", text)):
            if not ch_or_word:
                continue
            if ch_or_word.isspace():
                space = True  # 공백은 다음 조각 앞에 붙인다 ([강조] 경계를 넘어가도 유지)
                continue
            out.append((ch_or_word, hl, space))
            space = False
    return out


def wrap(parts, font, max_w, lang):
    """조각들을 max_w 안에 들어가게 줄로 나눈다. 반환: [[(글자, 강조)]]"""
    space_w = font.getlength(" ")
    lines, line, width = [], [], 0.0
    for tok, hl, space in _tokens(parts, lang):
        w = font.getlength(tok)
        gap = space_w if (space and line) else 0
        if line and width + gap + w > max_w and not (lang == "ja" and tok in _NO_START):
            lines.append(line)
            line, width, gap = [], 0.0, 0
        if gap:
            line.append((" ", line[-1][1] and hl))
        line.append((tok, hl))
        width += gap + w
    if line:
        lines.append(line)
    return lines


def _fit_title(title, lang, box_w, box_h, scale):
    """제목이 상자 안에 들어가는 가장 큰 글자 크기를 찾는다."""
    parts = parse_highlight(_EMOJI.sub("", title).strip())
    if not parts:
        return None, [], 0
    start = int(box_h * 0.42 * scale)
    for size in range(max(start, 24), 21, -2):
        font = font_for(lang, size)
        lines = wrap(parts, font, box_w, lang)
        lh = size * (1.08 if lang not in ("ko", "ja") else 1.22)
        too_wide = any(sum(font.getlength(t) for t, _ in ln) > box_w for ln in lines)
        if len(lines) * lh <= box_h and not too_wide:
            return font, lines, lh
    font = font_for(lang, 22)
    return font, wrap(parts, font, box_w, lang), 22 * 1.2


# ---------------------------------------------------------------- 로고

def _brand_layer(W, H, logo, brand, pos):
    """맨 위 로고(이미지) 또는 브랜드 이름(글자)."""
    if not logo and not brand:
        return None
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    margin, top = int(W * 0.05), int(H * 0.035)
    if logo is not None:
        lg = logo.convert("RGBA")
        lh = int(H * 0.06)
        lg = lg.resize((max(1, round(lg.width * lh / lg.height)), lh), Image.LANCZOS)
        if lg.width > W * 0.4:
            lg = lg.resize((int(W * 0.4), max(1, round(lg.height * W * 0.4 / lg.width))), Image.LANCZOS)
        x = {"left": margin, "right": W - margin - lg.width}.get(pos, (W - lg.width) // 2)
        layer.alpha_composite(lg, (x, top))
        return layer
    font = font_for("ko", int(H * 0.032), kind="ui")
    tw = font.getlength(brand)
    x = {"left": margin, "right": W - margin - tw}.get(pos, (W - tw) / 2)
    d = ImageDraw.Draw(layer)
    d.text((x, top), brand, font=font, fill=(255, 255, 255, 255), stroke_width=max(2, int(H * 0.003)),
           stroke_fill=(0, 0, 0, 160))
    return layer


# ---------------------------------------------------------------- 카드 합성

def compose(photo, title, lang="ko", ratio="1:1", logo=None, brand="", opts=None):
    """사진 + 제목 → 인스타 카드 (PIL RGB 이미지).

    photo: PIL 이미지, logo: PIL 이미지(RGBA) 또는 None, brand: 로고가 없을 때 쓰는 브랜드 이름.
    opts: CARD_DEFAULTS의 키 (글자 크기, 그라데이션, 사진 비율, 대문자, 보정, 로고 위치).
    """
    o = {**CARD_DEFAULTS, **(opts or {})}
    W, H = RATIOS.get(ratio, RATIOS["1:1"])
    pr = min(0.85, max(0.4, float(o["photo_ratio"])))
    ph = int(H * pr)
    grad = min(1.0, max(0.0, float(o["gradient"])))
    # 사진은 그라데이션이 걸칠 만큼 조금 더 길게 깔고, 아래는 검은 바탕
    overlap = int(H * 0.12)
    img = enhance(photo) if o["enhance"] else photo
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(_cover(img, W, ph + overlap, _focus_y(photo)), (0, 0))

    # 그라데이션: 사진 아래쪽부터 검게. 진하기가 높을수록 더 위에서부터, 더 어둡게
    g_h = int(H * (0.12 + 0.30 * grad)) + overlap
    g_top = ph + overlap - g_h
    alpha = np.zeros((H, 1), np.float32)
    alpha[g_top:g_top + g_h, 0] = np.linspace(0, 1, g_h, dtype=np.float32) ** 1.5
    alpha[g_top + g_h:, 0] = 1.0
    top_shade = int(H * 0.16)  # 로고가 잘 보이게 위쪽도 살짝 어둡게
    alpha[:top_shade, 0] = np.maximum(alpha[:top_shade, 0],
                                      np.linspace(0.35 * (0.5 + grad / 2), 0, top_shade, dtype=np.float32))
    a = np.repeat(alpha, W, axis=1)
    arr = np.asarray(canvas, dtype=np.float32) * (1 - a[:, :, None])
    canvas = Image.fromarray(arr.clip(0, 255).astype(np.uint8))

    # 제목
    if lang not in ("ko", "ja") and o["upper"]:
        title = title.upper()
    margin = int(W * 0.06)
    box_top = ph - int(H * 0.06)
    box_h = H - box_top - int(H * 0.06)
    font, lines, lh = _fit_title(title or "", lang, W - margin * 2, box_h, float(o["font_scale"]))
    d = ImageDraw.Draw(canvas)
    if lines:
        block_h = len(lines) * lh
        y = box_top + (box_h - block_h) / 2
        for ln in lines:
            lw = sum(font.getlength(t) for t, _ in ln)
            x = (W - lw) / 2
            for tok, hl in ln:
                d.text((x, y), tok, font=font, fill=HIGHLIGHT if hl else TEXT,
                       stroke_width=max(1, font.size // 28), stroke_fill=(0, 0, 0))
                x += font.getlength(tok)
            y += lh

    brand_layer = _brand_layer(W, H, logo, brand, o["logo_pos"])
    if brand_layer is not None:
        canvas = Image.alpha_composite(canvas.convert("RGBA"), brand_layer).convert("RGB")
    return canvas


def to_jpeg(img, quality=92):
    """인스타 업로드 규격: JPEG, sRGB, 8MB 이하."""
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()
