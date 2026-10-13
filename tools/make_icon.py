"""앱 아이콘 생성 — 웹앱과 같은 스타일 (노란 둥근 사각형 + 굵은 검은 테두리).

- assets/icon.ico, assets/icon.png, web/icon.png: 영상 자동 편집기
- assets/icon_insta.ico, assets/icon_insta.png: 인스타 게시물 만들기 (바탕화면 아이콘)
"""
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
    """인스타 게시물 아이콘: 겹쳐진 사진 카드 + 아래쪽 노란 제목 줄 (만들어지는 카드의 모습)."""
    s = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, b = int(200 * s), int(46 * s)
    d.rounded_rectangle([110 * s, 110 * s, 990 * s, 990 * s], radius=r, fill=INK)          # 그림자
    d.rounded_rectangle([40 * s, 40 * s, 920 * s, 920 * s], radius=r, fill=CORAL, outline=INK, width=b)
    # 뒤쪽 카드 (여러 장 = 여러 언어)
    d.rounded_rectangle([340 * s, 150 * s, 800 * s, 720 * s], radius=int(55 * s), fill=CREAM, outline=INK,
                        width=int(32 * s))
    # 앞쪽 카드
    card = [150 * s, 230 * s, 650 * s, 850 * s]
    d.rounded_rectangle(card, radius=int(55 * s), fill=CREAM, outline=INK, width=int(36 * s))
    # 사진 영역 (하늘 + 산)
    d.rectangle([180 * s, 262 * s, 620 * s, 610 * s], fill=(110, 170, 225, 255))
    d.polygon([(180 * s, 610 * s), (360 * s, 380 * s), (540 * s, 610 * s)], fill=(79, 176, 165, 255))
    d.polygon([(400 * s, 610 * s), (520 * s, 450 * s), (620 * s, 610 * s)], fill=(46, 140, 132, 255))
    d.ellipse([520 * s, 300 * s, 600 * s, 380 * s], fill=YELLOW)                           # 해
    # 어두운 아래쪽 + 제목 두 줄 (노란 줄 = [강조])
    d.rectangle([180 * s, 610 * s, 620 * s, 822 * s], fill=INK)
    d.rounded_rectangle([225 * s, 660 * s, 575 * s, 712 * s], radius=int(26 * s), fill=YELLOW)
    d.rounded_rectangle([265 * s, 740 * s, 535 * s, 788 * s], radius=int(24 * s), fill=CREAM)
    return img


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
    print("ok")


if __name__ == "__main__":
    main()
