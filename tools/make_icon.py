"""앱 아이콘(assets/icon.ico, web/icon.png) 생성 — 웹앱과 같은 스타일 (노란 둥근 사각형 + 굵은 검은 테두리)."""
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


def main():
    big = draw(1024)
    (ROOT / "assets").mkdir(exist_ok=True)
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "icon.ico",
                                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "icon.png")
    big.resize((64, 64), Image.LANCZOS).save(ROOT / "web" / "icon.png")
    print("ok")


if __name__ == "__main__":
    main()
