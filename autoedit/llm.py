"""Claude API 호출: 설명 장면 기획, 음성 속 개인정보 탐지, 화면 텍스트 개인정보 판정, SVG 일러스트 생성."""
import json
import os

import anthropic

MODEL = "claude-opus-5-5"
POSITIONS = ["auto", "left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right", "center"]


class UserError(RuntimeError):
    """사용자에게 그대로 보여줄 오류 (스택 트레이스 없이)."""


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def ask(prompt, *, system, schema=None, effort="medium", max_tokens=32000):
    """prompt는 문자열 또는 content 블록 리스트(이미지 포함)."""
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        with _get_client().beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config=output_config,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise UserError("API 키가 올바르지 않습니다. 키를 다시 확인해 주세요.") from None
    except anthropic.BadRequestError as e:
        if "credit balance" in str(e):
            raise UserError("Anthropic 계정 크레딧이 부족합니다. console.anthropic.com → Plans & Billing에서 충전해 주세요.") from None
        raise
    except anthropic.RateLimitError:
        raise UserError("요청 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.") from None
    except anthropic.APIConnectionError:
        raise UserError("Claude API에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.") from None
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude가 요청을 거절했습니다.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Claude 응답이 길이 제한에 걸려 잘렸습니다.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text) if schema else text


# ---------------------------------------------------------------- 장면 기획 + 음성 개인정보

PLAN_SYSTEM = """You are a senior YouTube video editor for Korean talking-head videos.
You receive a time-coded transcript of an already silence/filler-trimmed video.

Task 1 — explainer inserts. Pick the moments where a visual would make the spoken explanation easier to
follow. The speaker stays on screen and the narration keeps playing, so each visual must match what is
being said during its window. Skip greetings, intros/outros and small talk. Two kinds:
- "object": 1–3 simple objects/icons on a transparent background that pop up beside the speaker — the
  thing being named (an app, a document, money, a house, a calendar, a phone), a key number, a check/cross.
  2–5 seconds. Use objects for most inserts; they are light and keep the speaker visible.
- "card": a small illustrated scene shown as a centered card over the video, for things that need a
  composition: step-by-step procedures, comparisons, cause→effect, eligibility conditions. 4–8 seconds.
Rules:
- start/end follow the speech: start when the thing is first mentioned (a line start or a moment inside a
  line), end when the speaker moves on. No overlaps, at least 3 seconds between inserts.
- `position`: "auto" (placed away from the speaker's face) unless the content needs a specific spot.
- `visual`: a concrete English description of what to draw (objects, characters' poses/expressions, arrows,
  layout). Never real people.
- `logo`: if the object stands for a specific real brand/service/agency (e.g. 복지로, 카카오톡, 국세청), its name —
  we draw a generic stand-in icon (never the real trademark) and the editor may swap in the real logo. Else "".
- `caption`: at most 10 Korean characters (e.g. "소득 70% 이하"), or "" when the picture speaks for itself —
  prefer "" for objects. `labels`: up to 3 tiny Korean/number labels (≤5 chars), only if essential.

Task 2 — spoken personal information. List phrases where the speaker says sensitive personal data that
must be bleeped: phone numbers, resident registration numbers, card/account numbers, exact home addresses,
passwords, or a private (non-public) third party's full name together with identifying details.
Do NOT flag the speaker's own channel name, public figures, companies, government programs, or generic
example numbers clearly presented as fake. `phrase` must be copied verbatim from that line's text."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start": {"type": "number"},
                "end": {"type": "number"},
                "kind": {"type": "string", "enum": ["object", "card"]},
                "position": {"type": "string", "enum": POSITIONS},
                "idea": {"type": "string", "description": "한국어로, 이 장면이 설명하는 핵심"},
                "visual": {"type": "string"},
                "logo": {"type": "string"},
                "caption": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["start", "end", "kind", "position", "idea", "visual", "logo", "caption", "labels"],
            "additionalProperties": False,
        }},
        "spoken_pii": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "line_id": {"type": "integer"},
                "phrase": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["line_id", "phrase", "kind"],
            "additionalProperties": False,
        }},
    },
    "required": ["scenes", "spoken_pii"],
    "additionalProperties": False,
}


def plan_edit(lines, total_duration, max_scenes, kinds=("object", "card")):
    body = "\n".join(f"L{l['id']} [{l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in lines)
    only = "" if len(kinds) == 2 else f' Use only kind "{kinds[0]}" for every insert.' if kinds else ""
    prompt = (f"Video length: {total_duration:.1f}s. Use at most {max_scenes} explainer inserts.{only}\n\n"
              f"<transcript>\n{body}\n</transcript>")
    return ask(prompt, system=PLAN_SYSTEM, schema=PLAN_SCHEMA, effort="medium")


# ---------------------------------------------------------------- 화면 텍스트 개인정보 판정

OCR_SYSTEM = """You review text strings that OCR found on screen in a Korean YouTube video.
Return the indices of strings that expose someone's sensitive personal information and should be blurred:
phone numbers, resident registration numbers, emails, card/bank account numbers, exact street addresses,
license plates, a private person's real name shown with personal details (e.g. on a document, ID, parcel label,
chat screenshot, delivery app), birthdates tied to a person. Do NOT flag UI labels, brand names, prices,
public organization phone numbers (e.g. 1588-xxxx call centers, 129, 1350), websites, or the video's own titles."""

OCR_SCHEMA = {
    "type": "object",
    "properties": {"pii_indices": {"type": "array", "items": {"type": "integer"}}},
    "required": ["pii_indices"],
    "additionalProperties": False,
}


def classify_ocr(texts):
    body = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    return set(ask(body, system=OCR_SYSTEM, schema=OCR_SCHEMA, effort="low", max_tokens=8000)["pii_indices"])


# ---------------------------------------------------------------- SVG 일러스트

SVG_COMMON = """You are an illustrator who draws explainer visuals for Korean YouTube videos as hand-written SVG.

Default style (used when no reference images are given): flat vector illustration with a hand-drawn cartoon feel.
- Palette (flat fills): #FF8A5B coral, #4FB0A5 teal, #FFD166 yellow, #6C8CD5 blue, #FFFFFF white, outlines #2E2E2E.
- Every shape gets a dark outline, stroke-width 6–8, stroke-linecap="round", stroke-linejoin="round".
- Characters: cute simple cartoon people — round heads, dot eyes, simple limbs — with clear, expressive poses.
- Hand-drawn wobble: define exactly this filter in <defs> and put the drawing (not text) inside <g filter="url(#hand)">:
  <filter id="hand" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency="0.012" numOctaves="2" seed="7" result="n"/><feDisplacementMap in="SourceGraphic" in2="n" scale="7" xChannelSelector="R" yChannelSelector="G"/></filter>

If reference images are attached, they define the style: match their palette, outline weight, shading
(flat / cel / halftone dots via <pattern>), character proportions, textures and overall mood as closely as SVG
allows. The references override the default palette. Never copy their content, text, characters or logos.

Text: minimal. Only the given caption (if non-empty) and the given labels — never sentences. Use
font-family="Malgun Gothic" font-weight="bold". Text must stay outside any filter group so it stays crisp.
Copy Korean text exactly.
Real brands/services: draw a generic stand-in icon that suggests the category; never reproduce a real trademark.
Technical: plain SVG 1.1 (g, path, rect, circle, ellipse, line, polyline, polygon, text, tspan, defs, filter,
clipPath, mask, pattern, linearGradient, radialGradient). No <image>, no external references, no CSS/<style>,
no foreignObject, no scripts, no animation. Under ~300 elements.
Output ONLY the SVG markup, starting with <svg and ending with </svg>."""

KIND_RULES = {
    "object": """This is an OBJECT STICKER composited over live video next to the speaker.
- TRANSPARENT background: no background rect, nothing behind the objects.
- Draw 1–3 objects as one compact, centered die-cut sticker filling ~80% of the canvas.
- Give the whole sticker a thick white outer outline (draw the silhouette first with stroke #FFFFFF,
  stroke-width ~28, then the colored artwork on top) so it reads over any video.
- No scenery, no ground line. Caption (if any) as a small white rounded label under the objects,
  font-size ≥ 9% of canvas height.""",
    "card": """This is an ILLUSTRATED CARD shown centered over the video with rounded corners (the frame is added later).
- Fill the whole canvas with a background (a full-size rect plus optional simple shapes) in the style.
- ONE clear idea readable in 3 seconds: large central subject, generous empty space, nothing important within
  7% of the edges. Arrows/numbers for processes, side-by-side for comparisons.
- Caption (if any): top or bottom center, font-size ≥ 8% of canvas height, on a rounded sticker shape.
  Labels: ≥ 4.5% of canvas height.""",
}


def draw_svg(scene, width, height, context, refs=(), error=None):
    """refs: [(media_type, base64)] 그림체 참고 이미지."""
    content = []
    for i, (mt, data) in enumerate(refs):
        block = {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}}
        if i == len(refs) - 1:
            block["cache_control"] = {"type": "ephemeral"}  # 여러 장면을 그리는 동안 참고 이미지 재사용
        content.append(block)
    text = (("Style reference images are attached above.\n" if refs else "")
            + f"{KIND_RULES[scene['kind']]}\n\n"
            f'Canvas: <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">\n'
            f"What the narrator is saying (Korean): {context}\n"
            f"Point to explain (Korean): {scene['idea']}\n"
            f"Draw: {scene.get('visual') or scene['idea']}\n"
            + (f"Stands for the real service \"{scene['logo']}\" — draw a generic stand-in icon.\n" if scene.get("logo") else "")
            + f"Caption: {scene['caption'] or '(none)'}\n"
            f"Labels: {', '.join(scene.get('labels') or []) or '(none)'}")
    if error:
        text += f"\n\nYour previous SVG failed to render with this error, avoid it: {error}"
    content.append({"type": "text", "text": text})
    return ask(content, system=SVG_COMMON, effort="medium")


# ---------------------------------------------------------------- 자막 교정

PROOF_SYSTEM = """You proofread Korean YouTube subtitles produced by speech recognition.
Fix misrecognized words (use the surrounding lines and the glossary), spelling, spacing (띄어쓰기), and number
formatting (e.g. "이십만 원" → "20만 원", "육십 퍼센트" → "60%"). Do not paraphrase, summarize, add or remove
content, and do not merge or split lines — keep each line's meaning and roughly its length. Leave lines that are
already correct unchanged. Lines spoken in another language (e.g. English) stay in that language — only fix
obvious recognition errors there, never translate. Return exactly one output line per input line, in the same order,
without numbers."""

PROOF_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
    "additionalProperties": False,
}


TRANSLATE_SYSTEM = """You prepare Korean subtitles for a YouTube video from speech-recognition lines.
- Lines in another language (e.g. English): translate into natural, conversational Korean subtitle style
  (구어체, short and easy to read). Translate meaning, not word by word, but don't add or drop content.
  Lines are consecutive pieces of the same conversation, so a sentence may be split across lines — translate
  each piece so the lines read naturally in order, keeping each line's content in that line.
- Lines mixing languages (e.g. "to be honest 네"): turn the whole line into Korean ("솔직히 말하면 네").
- The result must contain no English sentences or phrases at all; keep only proper nouns/brands that
  Koreans normally write in Latin letters (e.g. "BTS", "iPhone").
- Lines already in Korean: only fix misrecognized words, spelling, spacing (띄어쓰기) and number formatting
  (e.g. "이십만 원" → "20만 원"). Do not paraphrase them.
- Keep names, brands and the glossary terms correct. Never merge or split lines.
Return exactly one output line per input line, in the same order, without numbers."""


def proofread(texts, glossary="", translate=False):
    """자막 교정. translate=True면 한국어가 아닌 줄을 자연스러운 한국어 자막으로 번역."""
    body = (f"Glossary: {glossary}\n\n" if glossary.strip() else "") + \
        f"{len(texts)} lines:\n" + "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    system = TRANSLATE_SYSTEM if translate else PROOF_SYSTEM
    out = ask(body, system=system, schema=PROOF_SCHEMA, effort="medium")["lines"]
    if len(out) != len(texts):
        raise UserError(f"자막 교정 결과의 줄 수가 맞지 않아 적용하지 않았습니다 ({len(texts)} → {len(out)}).")
    return [o.strip() or t for o, t in zip(out, texts)]
