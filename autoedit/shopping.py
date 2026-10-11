"""쇼핑 쇼츠 모드: 직접 찍은 클립 + 대본 → 세로 쇼츠 완성본(1080x1920).

대본 형식 (한 줄 = 한 컷, 위에서부터 순서대로):

    키워드: 쫀득
    아이가 간식 사 달라고 조르는데
    고구마가 남아 있길래
    3_치즈넣기.mp4 | 이걸 넣고 치즈볼을 만들어 봤어요
    레시피 궁금하면 '쫀득' 남겨 주세요

- 클립은 이름 순서대로 줄에 하나씩 붙는다. "파일이름 | 자막"처럼 쓰면 그 클립을 쓴다.
- 자막: 화면 가운데보다 살짝 아래, 흰 글자 + 검은 테두리, 한 줄 12자 이내, 키워드는 노란색.
- 컷: 말이 2초를 넘으면 같은 클립의 다른 부분으로 한 번 더 끊어 1~2초마다 화면이 바뀐다.
- 소리: AI 목소리(edge-tts, 무료) + 클립의 실제 소리(작게) + 배경음악(있으면, 더 작게) → -14 LUFS.
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
from .media import FFMPEG, SR, probe, run, write_wav
from .subtitles import FONT_CANDIDATES, write_srt

W, H, FPS = 1080, 1920, 30
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
VOICES = {"여성": "ko-KR-SunHiNeural", "남성": "ko-KR-InJoonNeural"}
DEFAULT_KEYWORDS = ("촉촉", "바삭", "탱글", "쫀득")
MAX_CHARS = 12        # 자막 한 줄 최대 글자 수
MAX_SHOT = 2.0        # 한 화면이 이보다 길면 같은 클립의 다른 부분으로 한 번 더 끊는다
MIN_SHOT = 1.0
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
            if Path(left).suffix.lower() in VIDEO_EXTS:
                clip, line = left, right
        line = re.sub(r"^\d+\s*[.)]\s*", "", line)  # "1. " 같은 번호는 뺀다
        if line:
            lines.append({"clip": clip, "text": line})
    if not lines:
        raise UserError("대본이 비어 있습니다. 한 줄에 한 컷씩 자막을 적어 주세요.")
    if not keyword:  # 키워드를 안 적었으면 대본에 나온 식감 단어를 쓴다
        joined = " ".join(l["text"] for l in lines)
        keyword = next((k for k in DEFAULT_KEYWORDS if k in joined), "")
    return keyword, lines


def wrap(text, max_chars=MAX_CHARS):
    """띄어쓰기 기준으로 한 줄 max_chars 이내로 나눈다. 너무 긴 단어는 글자 단위로 자른다."""
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
    return out


def subtitle_chunks(text):
    """자막을 화면에 한 번에 보일 덩어리(최대 2줄)로 나눈다."""
    rows = wrap(text)
    return [rows[i:i + 2] for i in range(0, len(rows), 2)]


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


def subtitle_sprite(rows, keyword):
    """흰 글자 + 검은 테두리 + 부드러운 그림자, 키워드는 노란색. 키워드가 있는 자막은 조금 더 크게."""
    big = bool(keyword) and any(keyword in r for r in rows)
    fs = int(W * (0.082 if big else 0.07))
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


# ---------------------------------------------------------------- 목소리

def tts(text, voice, rate, out_path):
    """edge-tts(무료)로 한국어 AI 목소리 mp3를 만든다."""
    try:
        import edge_tts
    except ImportError:
        raise UserError("AI 목소리 모듈이 없습니다. 명령창에서 'pip install edge-tts'를 실행해 주세요.") from None
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    comm = edge_tts.Communicate(text, VOICES.get(voice, voice), rate=rate, proxy=proxy)
    for attempt in range(3):
        try:
            asyncio.run(comm.save(str(out_path)))
            return
        except Exception as e:  # 일시적인 연결 오류는 다시 시도
            if attempt == 2:
                raise UserError(f"AI 목소리를 만들지 못했습니다 (인터넷 연결 확인): {type(e).__name__}") from None


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


def fade(a, sec=0.03):
    n = min(int(sec * SR), len(a) // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        a[:n] *= ramp
        a[-n:] *= ramp[::-1]
    return a


# ---------------------------------------------------------------- 만들기

def list_clips(folder):
    return sorted((p for p in Path(folder).iterdir() if p.suffix.lower() in VIDEO_EXTS),
                  key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.stem)])


def assign_clips(lines, clips):
    """대본 줄마다 클립을 붙인다. 이름을 적은 줄은 그 클립, 나머지는 남은 클립을 순서대로."""
    by_name = {c.name: c for c in clips}
    named = {l["clip"] for l in lines if l["clip"]}
    missing = [n for n in named if n not in by_name]
    if missing:
        raise UserError(f"대본에 적은 클립이 폴더에 없습니다: {', '.join(missing)}")
    rest = iter(c for c in clips if c.name not in named)
    out = []
    for l in lines:
        clip = by_name[l["clip"]] if l["clip"] else next(rest, None)
        if clip is None:
            need = len(lines) - len(named)
            raise UserError(f"클립이 부족합니다. 대본은 {len(lines)}줄인데 클립이 {len(clips)}개예요 "
                            f"(이름을 안 적은 줄 {need}개에 클립이 하나씩 필요).")
        out.append(clip)
    return out


def make_short(clips, script, out, work, keyword=None, voice="여성", rate="+10%", bgm=None,
               clip_volume=0.35, bgm_volume=0.12, voice_volume=1.0):
    """클립 목록 + 대본 글 → 완성본 mp4 (+ 같은 이름의 .srt). voice="없음"이면 AI 목소리 없이 클립 소리만."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    keyword, lines = parse_script(script, keyword)
    clips = assign_clips(lines, [Path(c) for c in clips])
    print(f"대본 {len(lines)}줄 · 키워드 '{keyword or '없음'}' · 목소리 {voice}")

    # 1) 줄마다 목소리를 만들고 길이를 정한다
    speech, durs = [], []
    for i, l in enumerate(lines):
        jobctl.check()
        if voice != "없음":
            mp3 = work / f"voice_{i:02d}.mp3"
            tts(l["text"], voice, rate, mp3)
            a = decode(mp3)
            dur = max(MIN_SHOT, len(a) / SR + 0.12)
        else:
            a = np.zeros((0, 2), np.float32)
            dur = max(1.2, len(l["text"]) * 0.17 + 0.4)  # 읽기 좋은 시간
        speech.append(a)
        durs.append(round(dur * FPS) / FPS)  # 프레임 단위로 맞춰 오디오와 영상이 어긋나지 않게
        print(f"  {i + 1:2d}. {dur:4.1f}초  {l['text']}")

    # 2) 화면 조각: 2초가 넘는 줄은 같은 클립의 앞부분 → 뒷부분으로 한 번 더 끊는다
    shots, cues, t = [], [], 0.0
    for i, (l, clip, dur) in enumerate(zip(lines, clips, durs)):
        clen = probe(clip)["duration"]
        n = 2 if dur > MAX_SHOT and clen >= dur + 0.6 else 1
        part = round(dur / n * FPS) / FPS
        for k in range(n):
            d = part if k < n - 1 else round((dur - part * (n - 1)) * FPS) / FPS
            # 첫 조각은 클립 앞(0.2초 뒤)부터, 둘째 조각은 클립 뒤쪽에서 → 같은 클립이라도 화면이 바뀐다
            off = 0.2 if k == 0 else max(0.2, clen - d - 0.2)
            off = min(off, max(0.0, clen - d))
            shots.append({"clip": clip, "start": off, "dur": d, "line": i})
        chunks = subtitle_chunks(l["text"])
        total = sum(len("".join(c)) for c in chunks)
        ct = t
        for c in chunks:
            cd = dur * len("".join(c)) / total
            cues.append({"start": round(ct, 2), "end": round(ct + cd - 0.03, 2), "rows": c, "text": " ".join(c),
                         "line": i})
            ct += cd
        t += dur
    total_dur = t

    # 3) 화면 조각마다 세로로 맞추고 자막을 얹어 인코딩
    seg_files = []
    for j, s in enumerate(shots):
        jobctl.check()
        shot_start = sum(x["dur"] for x in shots[:j])
        overlays = []
        for k, c in enumerate(cues):
            a, b = max(c["start"], shot_start), min(c["end"], shot_start + s["dur"])
            if b > a and c["line"] == s["line"]:
                png = work / f"sub_{k:02d}.png"
                subtitle_sprite(c["rows"], keyword).save(png)  # 대본을 고쳐 다시 돌릴 수 있으니 항상 새로 그림
                overlays.append((png, a - shot_start, b - shot_start))
        seg = work / f"shot_{j:02d}.mp4"
        inputs = ["-ss", f"{s['start']:.3f}", "-i", str(s["clip"])]
        fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration={s['dur']:.3f}[v0]")
        for k, (png, a, b) in enumerate(overlays):
            inputs += ["-i", str(png)]
            fc += (f";[v{k}][{k + 1}:v]overlay=x=(W-w)/2:y={H * SUB_CY:.0f}-h/2:"
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
    if bgm:
        music = decode(bgm)
        if len(music):
            reps = int(np.ceil(n_total / len(music)))
            music = np.tile(music, (reps, 1))[:n_total]
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
