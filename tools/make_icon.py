"""앱 아이콘 생성 - 웹앱과 같은 스타일 (노란 둥근 사각형 + 굵은 검은 테두리).

- assets/icon.ico, assets/icon.png, web/icon.png: 영상 자동 편집기
- assets/icon_insta.ico, assets/icon_insta.png: 인스타 게시물 만들기 (바탕화면 아이콘)
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
INK, YELLOW, CREAM, CORAL = (21, 22, 27, 255), (246, 201, 69, 255), (255, 248, 231, 255), (255, 122, 89, 255)


def draw(size=1024):
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, b = int(200 * s), int(46 * s)
    # 그림자 → 본체
    d.rounded_rectangle([110 * s, 110 * s, 990 * s, 990 * s], radius=r, fill=INK)
    d.rounded_rectangle([40 * s, 40 * s, 920 * s, 920 * s], radius=r, fill=YELLOW, outline=INK, width=b)
    # 필름 화면 (크림색 카드)
    d.rounded_rectangle([190 * s, 250 * s, 770 * s, 690 * s], radius=int(60 * s), fill=CREAM, outline=INK, width=int(36 * s))
    # 재생 버튼
    d.polygon([(410 * s, 355 * s), (410 * s, 585 * s), (600 * s, 470 * s)], fill=INK)
    # 가위 컷 표시 (코랄 점선 → 자동 컷)
    for x in range(200, 780, 120):
        d.rounded_rectangle([x * s, 770 * s, (x + 70) * s, 810 * s], radius=int(20 * s), fill=CORAL, outline=INK,
                            width=int(12 * s))
    # 반짝이 (자동 = 마법)
    cx, cy, k = 790 * s, 210 * s, 95 * s
    d.polygon([(cx, cy - k), (cx + k * .28, cy - k * .28), (cx + k, cy), (cx + k * .28, cy + k * .28),
               (cx, cy + k), (cx - k * .28, cy + k * .28), (cx - k, cy), (cx - k * .28, cy - k * .28)],
              fill=CREAM, outline=INK, width=int(16 * s))
    return img


def draw_insta(size=1024):
    """인스타 게시물 아이콘: 흔히 쓰는 카메라 모양 (둥근 사각 테두리 + 렌즈 + 점).

    인스타그램 로고의 보라-분홍-주황 그라데이션은 상표라서 쓰지 않고, 이 앱의 색(산호색·노랑)을 쓴다.
    """
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, b = int(200 * s), int(46 * s)
    d.rounded_rectangle([110 * s, 110 * s, 990 * s, 990 * s], radius=r, fill=INK)          # 그림자
    d.rounded_rectangle([40 * s, 40 * s, 920 * s, 920 * s], radius=r, fill=CORAL, outline=INK, width=b)
    # 카메라 몸통 (둥근 사각 테두리)
    d.rounded_rectangle([185 * s, 185 * s, 775 * s, 775 * s], radius=int(165 * s), fill=CREAM,
                        outline=INK, width=int(56 * s))
    # 렌즈
    cx = cy = 480 * s
    rad = 152 * s
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=CORAL, outline=INK, width=int(52 * s))
    d.ellipse([cx - 62 * s, cy - 86 * s, cx + 16 * s, cy - 8 * s], fill=CREAM)              # 빛 반사
    # 플래시 점
    d.ellipse([640 * s, 258 * s, 714 * s, 332 * s], fill=YELLOW, outline=INK, width=int(22 * s))
    return img


def draw_setup(size=1024):
    """설치·복구 아이콘: 회색 바탕 + 공구(렌치) 모양."""
    s = size / 1024
    gray = (209, 203, 189, 255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, b = int(200 * s), int(46 * s)
    d.rounded_rectangle([110 * s, 110 * s, 990 * s, 990 * s], radius=r, fill=INK)          # 그림자
    d.rounded_rectangle([40 * s, 40 * s, 920 * s, 920 * s], radius=r, fill=gray, outline=INK, width=b)
    # 톱니바퀴
    cx, cy, rad = 400 * s, 400 * s, 170 * s
    for i in range(8):
        a = math.radians(i * 45)
        d.regular_polygon((cx + rad * 0.95 * math.cos(a), cy + rad * 0.95 * math.sin(a), 62 * s), 4,
                          rotation=i * 45, fill=CREAM, outline=INK, width=int(22 * s))
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=CREAM, outline=INK, width=int(36 * s))
    d.ellipse([cx - 62 * s, cy - 62 * s, cx + 62 * s, cy + 62 * s], fill=gray, outline=INK, width=int(30 * s))
    img.alpha_composite(_screwdriver(size), (0, 0))
    return img


def _screwdriver(size):
    """드라이버를 세로로 그린 뒤 45도 돌려서 얹는다 (기울어진 사각형을 직접 그리기 어려우므로)."""
    s = size / 1024
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, w = 640 * s, 88 * s                       # 중심선, 손잡이 너비
    d.rounded_rectangle([x - w, 470 * s, x + w, 800 * s], radius=int(42 * s),       # 손잡이
                        fill=CORAL, outline=INK, width=int(26 * s))
    d.rectangle([x - 30 * s, 300 * s, x + 30 * s, 490 * s], fill=CREAM, outline=INK, width=int(22 * s))  # 축
    d.polygon([(x - 54 * s, 300 * s), (x + 54 * s, 300 * s), (x + 34 * s, 232 * s), (x - 34 * s, 232 * s)],
              fill=CREAM, outline=INK, width=int(22 * s))                           # 날
    return layer.rotate(-45, resample=Image.BICUBIC, center=(640 * s, 520 * s))


def save(img, stem, web=False):
    (ROOT / "assets").mkdir(exist_ok=True)
    small = img.resize((256, 256), Image.LANCZOS)
    small.save(ROOT / "assets" / f"{stem}.ico",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    small.save(ROOT / "assets" / f"{stem}.png")
    if web:
        img.resize((64, 64), Image.LANCZOS).save(ROOT / "web" / "icon.png")


def main():
    save(draw(1024), "icon", web=True)
    save(draw_insta(1024), "icon_insta")
    save(draw_setup(1024), "icon_setup")
    print("ok")


if __name__ == "__main__":
    main()
