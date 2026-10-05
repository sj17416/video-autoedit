"""OpenAI 이미지 생성(gpt-image) — '그림 생성: OpenAI' 선택 시.

Claude(SVG)보다 그림이 풍부하고, 그림체 참고 이미지를 직접 따라 그린다.
그림 모델은 한글 글자를 틀리게 쓰는 일이 많아서 그림 안에는 글자를 넣지 않고, 캡션은 따로 합성한다.
"""
import base64
import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .llm import UserError

MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
STYLE_DEFAULT = ("flat vector illustration with a hand-drawn cartoon feel: bold dark outlines with slightly wobbly "
                 "hand-drawn lines, flat fills in a warm limited palette (coral, teal, mustard yellow, soft blue), "
                 "cute simple characters with round heads and dot eyes")
FONT = next((f for f in ["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"] if Path(f).exists()), None)


def available():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _prompt(scene, has_refs):
    style = ("Match the art style of the attached reference images as closely as possible (palette, line weight, "
             "shading, textures, character design). Do not copy their content, characters or text."
             if has_refs else f"Style: {STYLE_DEFAULT}.")
    if scene["kind"] == "object":
        layout = ("A single die-cut sticker: 1-3 objects grouped and centered, filling most of the square, with a "
                  "thick white outer border around the whole silhouette. Transparent background, no scenery, "
                  "no ground shadow.")
    else:
        layout = ("A simple explainer illustration with a plain background: one clear idea readable in 3 seconds, "
                  "large central subject, generous empty space, nothing important near the edges.")
    logo = (f' It represents the service "{scene["logo"]}": draw a generic icon for it, not the real logo.'
            if scene.get("logo") else "")
    return (f"{layout} {style} Draw: {scene.get('visual') or scene['idea']}. Meaning (Korean): {scene['idea']}.{logo} "
            "Absolutely no text, letters, numbers or words anywhere in the image.")


def generate(scene, refs, quality="medium"):
    """PNG 바이트 반환. refs: [(media_type, base64)] 그림체 참고 이미지."""
    import openai

    client = openai.OpenAI()
    obj = scene["kind"] == "object"
    common = dict(model=MODEL, prompt=_prompt(scene, bool(refs)), size="1024x1024" if obj else "1536x1024",
                  background="transparent" if obj else "opaque", quality=quality, output_format="png")
    try:
        if refs:
            files = [(f"ref{i}.jpg", base64.b64decode(data), mt) for i, (mt, data) in enumerate(refs)]
            r = client.images.edit(image=files, **common)
        else:
            r = client.images.generate(**common)
    except openai.AuthenticationError:
        raise UserError("OpenAI API 키가 올바르지 않습니다. 앱 왼쪽 '설정'에서 키를 다시 넣어 주세요.") from None
    except openai.PermissionDeniedError as e:
        if "verif" in str(e).lower():
            raise UserError("이 그림 모델은 OpenAI 조직 인증이 필요합니다: platform.openai.com → Settings → "
                            "Organization → Verify. 또는 옵션에서 '그림 생성: Claude'를 고르세요.") from None
        raise UserError(f"OpenAI 권한 오류: {str(e)[:200]}") from None
    except openai.BadRequestError as e:
        raise UserError(f"OpenAI 요청 오류: {str(e)[:200]}") from None
    except openai.RateLimitError as e:
        if "quota" in str(e) or "billing" in str(e):
            raise UserError("OpenAI 계정 크레딧이 부족합니다. platform.openai.com → Billing에서 충전해 주세요.") from None
        raise UserError("OpenAI 요청 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.") from None
    except openai.APIConnectionError:
        raise UserError("OpenAI API에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.") from None
    return add_caption(base64.b64decode(r.data[0].b64_json), scene.get("caption", ""), obj)


def add_caption(png, caption, is_object):
    """캡션을 흰 둥근 스티커 라벨로 합성 (오브젝트: 아래, 카드: 위쪽 가운데)."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if is_object and img.getbbox():  # 투명 여백을 잘라내서 캡션이 그림 바로 아래 붙게
        img = img.crop(img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox() or img.getbbox())
    if not caption or not FONT:
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    W, H = img.size
    fs = int(H * (0.085 if is_object else 0.075))
    font = ImageFont.truetype(FONT, fs)
    tw = int(font.getlength(caption))
    pad_x, pad_y, border = int(fs * 0.6), int(fs * 0.3), max(4, fs // 12)
    lw, lh = tw + pad_x * 2, fs + pad_y * 2
    if is_object:  # 아래쪽에 라벨이 들어갈 공간을 늘림
        canvas = Image.new("RGBA", (max(W, lw + 20), H + lh + 20), (0, 0, 0, 0))
        canvas.paste(img, ((canvas.width - W) // 2, 0))
        x0, y0 = (canvas.width - lw) // 2, H + 4
    else:
        canvas = img
        x0, y0 = (W - lw) // 2, int(H * 0.06)
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x0, y0, x0 + lw, y0 + lh], radius=lh // 2, fill=(255, 255, 255, 255),
                        outline=(46, 46, 46, 255), width=border)
    d.text((x0 + pad_x, y0 + pad_y - fs * 0.1), caption, font=font, fill=(46, 46, 46, 255))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()
