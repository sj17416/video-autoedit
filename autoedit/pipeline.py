"""편집 프로젝트: 단계별 실행 + 중간 결과 저장. CLI(main.py)와 웹앱(app.py)이 함께 쓴다."""
import json
import math
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import cutplan, illustrate, imagegen, llm, media, privacy, render, subtitles, transcribe

ROOT = Path(__file__).resolve().parent.parent
STYLE_DIR = ROOT / "style_refs"
CONFIG = ROOT / "config.json"

DEFAULTS = {
    # 옵션
    "cut": True,           # 무음·추임새 컷
    "cards": True,         # 설명 일러스트 (카드)
    "objects": True,       # 로고·아이콘 오버레이 (투명 스티커)
    "mosaic": True,        # 화면 개인정보 모자이크
    "face_mosaic": False,  # 모든 얼굴 모자이크
    "beep": True,          # 말로 나온 개인정보 가리기
    "card_mode": "card",   # card(중앙 카드) / full(전체 화면)
    "whisper": "turbo",    # turbo(빠르고 정확) / medium / large-v3 / small
    "image_engine": "auto",  # auto / claude(SVG) / openai(이미지)
    "image_quality": "medium",  # OpenAI 그림 화질: low(빠름) / medium / high
    "speed": "fast",       # fast(1080p·30fps) / balanced(1080p·원본fps) / best(원본 그대로)
    # 세부 조절
    "language": "ko",      # ko / auto (영어가 섞인 영상)
    "vocab": "",           # 자주 나오는 용어 (쉼표 구분) → 음성 인식/자막 교정에 사용
    "max_pause": 0.45,     # 이 길이 이하의 쉼은 자르지 않음
    "keep_pause": 0.25,    # 긴 쉼을 자를 때 남길 호흡
    "scene_every": 20,     # 설명 이미지 최대 빈도 (N초에 1개)
    "scan_mode": "fast",   # fast(키프레임) / precise(초당 scan_fps장)
    "scan_fps": 1,         # 정밀 스캔 빈도
    "pii_audio": "beep",   # beep(삐) / mute(무음)
    "card_dim": 0.35,      # 카드가 뜰 때 배경 영상 어둡게
    "subtitles": True,     # 자막을 영상에 입히기
    "sub_style": "outline",  # outline(외곽선) / box(반투명 상자)
    "sub_size": 1.0,       # 자막 크기 배율
    "proofread": True,     # 전체 자동 실행 때 Claude로 자막 교정
    "translate_ko": True,  # 외국어(영어 등)로 말한 부분은 한국어 자막으로 번역
    "loudnorm": True,      # 유튜브 권장 음량(-14 LUFS)으로 평준화
}
STEPS = ["transcribe", "cut", "plan", "illustrate", "scan", "render"]
STEP_NAMES = {"transcribe": "음성 인식", "cut": "컷 편집", "plan": "장면 기획", "illustrate": "이미지 생성",
              "scan": "개인정보 스캔", "render": "최종 렌더링"}
# 단계를 다시 하면 함께 지워야 하는 결과물
RESETS = {"transcribe": ["transcript.json", "cutplan.json", "subtitles.json", "plan.json", "ocr.json",
                         "pii_flagged.json", "scenes"],
          "cut": ["cutplan.json", "subtitles.json", "plan.json", "ocr.json", "pii_flagged.json", "scenes"],
          "plan": ["plan.json", "scenes"], "illustrate": ["scenes"],
          "scan": ["ocr.json", "pii_flagged.json"], "render": []}


def load_json(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_json(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def locate_phrase(words, phrase):
    """문장 속 단어들 중 phrase를 덮는 가장 짧은 연속 구간 → 원본 시간 (start, end)."""
    target = cutplan.norm(phrase)
    if not target:
        return None
    best = None
    for i in range(len(words)):
        acc = ""
        for j in range(i, len(words)):
            acc += cutplan.norm(words[j]["word"])
            if target in acc:
                if best is None or j - i < best[1] - best[0]:
                    best = (i, j)
                break
    if best is None:
        return None
    return words[best[0]]["start"] - 0.05, words[best[1]]["end"] + 0.05


PROJECTS = ROOT / "projects"


def new_project_id():
    base = time.strftime("%Y%m%d-%H%M%S")
    pid, n = base, 2
    while (PROJECTS / pid).exists():
        pid, n = f"{base}-{n}", n + 1
    return pid


def load_project(pid):
    """projects/<pid>/project.json → Project. source가 절대경로면 원본 파일을 그대로 쓴다."""
    d = PROJECTS / pid
    info = load_json(d / "project.json")
    if not info:
        return None
    src = Path(info["source"])
    return Project(src if src.is_absolute() else d / src, d / "work", info["name"])


def project_for_file(src):
    """명령줄로 넣은 영상 → 같은 파일의 기존 프로젝트를 이어 쓰거나 새로 만든다 (원본은 복사하지 않음)."""
    src = Path(src).resolve()
    for d in sorted(PROJECTS.glob("*")) if PROJECTS.exists() else []:
        info = load_json(d / "project.json")
        if info and Path(info["source"]).is_absolute() and Path(info["source"]) == src:
            return d.name
    pid = new_project_id()
    (PROJECTS / pid).mkdir(parents=True)
    save_json(PROJECTS / pid / "project.json", {"name": src.name, "source": str(src)})
    return pid


def is_foreign(text):
    """자막 한 줄이 외국어(주로 영어)인지: 알파벳 단어가 있고 한글보다 알파벳이 많으면."""
    latin = len(re.findall(r"[A-Za-z]", text))
    return bool(re.search(r"[A-Za-z]{2,}", text)) and latin > len(re.findall(r"[가-힣]", text))


def output_dir():
    """완성본 저장 폴더 (기본: 프로그램 폴더의 상위 폴더 = 이집트 폴더)."""
    d = Path(load_json(CONFIG, {}).get("output_dir") or ROOT.parent)
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_output_dir(path):
    d = Path(path).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    save_json(CONFIG, {**load_json(CONFIG, {}), "output_dir": str(d)})
    return d


class Project:
    def __init__(self, src, work, name=None):
        self.src = Path(src)
        self.work = media.ensure_dir(work)
        self.name = name or self.src.name
        self.settings = {**DEFAULTS, **load_json(self.work / "settings.json", {})}
        self._meta = None

    # ------------------------------------------------------------ 저장/상태
    def p(self, name):
        return self.work / name

    @property
    def out(self):
        """완성본 경로: <저장 폴더>/<영상이름>_편집본.mp4"""
        return output_dir() / f"{Path(self.name).stem}_편집본.mp4"

    @property
    def scenes_dir(self):
        return self.work / "scenes"

    def save_settings(self, changes=None):
        for k, v in (changes or {}).items():
            if k in DEFAULTS:
                self.settings[k] = type(DEFAULTS[k])(v)
        save_json(self.p("settings.json"), self.settings)

    @property
    def meta(self):
        if self._meta is None:
            self._meta = load_json(self.p("meta.json"))
            if not self._meta:
                self._meta = media.probe(self.src)
                save_json(self.p("meta.json"), self._meta)
        return self._meta

    @property
    def out_format(self):
        """속도 설정에 따른 (출력 너비, 높이, fps)."""
        return media.render_format(self.meta, self.settings["speed"])

    @property
    def size(self):
        """출력(=설명 이미지·자막 배치 기준) 크기."""
        w, h, _ = self.out_format
        return w, h

    @property
    def kinds(self):
        return tuple(k for k, on in (("object", self.settings["objects"]), ("card", self.settings["cards"])) if on)

    def clear_cache(self):
        """이 영상의 작업 결과를 모두 지운다 (설정은 유지)."""
        for f in self.work.iterdir():
            if f.name in ("settings.json",):
                continue
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
        self._meta = None
        print("[캐시 삭제] 작업 결과를 모두 지웠습니다. 다음 실행은 처음부터 진행합니다.")

    def reset(self, step):
        for name in RESETS[step]:
            target = self.p(name)
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)

    def done(self, step):
        if step == "illustrate":
            plan = load_json(self.p("plan.json"))
            return bool(plan) and all(not s.get("enabled", True) or (s.get("image") and (self.scenes_dir / s["image"]).exists())
                                      for s in self.active_scenes(plan))
        if step == "render":
            return self.out.exists()
        return self.p({"transcribe": "transcript.json", "cut": "cutplan.json", "plan": "plan.json",
                       "scan": "ocr.json"}[step]).exists()

    def ensure(self, step):
        if not self.done(step):
            getattr(self, step)()

    # ------------------------------------------------------------ 단계
    def transcribe(self):
        print(f"[음성 인식] {self.name}")
        m = self.meta
        print(f"  {m['width']}x{m['height']}, {m['src_fps']:.2f}fps → {m['fps']}fps, {media.fmt_time(m['duration'])}")
        wav16 = self.p("audio16k.wav")
        if not wav16.exists():
            media.extract_wav16k(self.src, wav16)
        data = transcribe.transcribe(wav16, self.p("transcript.json"), self.settings["whisper"],
                                     self.settings["vocab"], self.settings["language"])
        print(f"  문장 {len(data['segments'])}개")

    def cut(self):
        self.ensure("transcribe")
        print("[컷 편집] 무음 + 필러워드")
        a16, sr16 = media.read_wav_mono(self.p("audio16k.wav"))
        dur = self.meta["duration"]
        if self.settings["cut"]:
            plan = cutplan.build_plan(load_json(self.p("transcript.json")), a16, sr16, dur,
                                      max_pause=self.settings["max_pause"], keep_pause=self.settings["keep_pause"])
        else:  # 컷 안 함: 전체 유지
            print("  (무음·추임새 컷 꺼짐 — 전체 유지)")
            plan = {"keep": [[0.0, dur]], "fillers": [], "rescued": 0, "duration": dur, "kept_duration": dur}
        save_json(self.p("cutplan.json"), plan)
        self.p("subtitles.json").unlink(missing_ok=True)  # 컷이 바뀌면 자막 시간도 다시
        tl = self.timeline
        print(f"  필러워드 {len(plan['fillers'])}개 제거, 컷 {max(0, len(tl.segs) - 1)}곳, "
              f"{media.fmt_time(self.meta['duration'])} → {media.fmt_time(tl.total)} "
              f"({(1 - tl.total / self.meta['duration']) * 100:.0f}% 단축)")

    @property
    def timeline(self):
        return cutplan.Timeline(load_json(self.p("cutplan.json"))["keep"], self.out_format[2],
                                media.grid_fps(self.meta["fps"]))

    def lines(self):
        """편집본 기준 시간의 문장 목록 (필러 제외)."""
        tl, out = self.timeline, []
        for si, seg in enumerate(load_json(self.p("transcript.json"))["segments"]):
            words = [w for w in seg["words"] if w["word"].strip() and not cutplan.is_filler(w["word"])]
            if not words:
                continue
            s, e = tl.to_new(words[0]["start"]), tl.to_new(words[-1]["end"])
            if e - s < 0.05:
                continue
            text = ""
            for w in words:  # "60" "%" → "60%"
                text += w["word"] if (text and not cutplan.norm(w["word"])) else f" {w['word']}"
            out.append({"id": si, "start": round(s, 2), "end": round(e, 2), "text": text.strip(), "words": words})
        return out

    def context_for(self, start, end, lines=None):
        return " ".join(l["text"] for l in (lines or self.lines()) if l["end"] > start and l["start"] < end)

    def clean_scenes(self, scenes, lines, total, min_gap=3.0):
        out = []
        for s in sorted(scenes, key=lambda s: s["start"]):
            lo, hi = (1.5, 6.0) if s["kind"] == "object" else (3.0, 10.0)
            start = max(0.0, min(s["start"], total - lo))
            end = min(total, max(s["end"], start + lo), start + hi)
            if end - start < 1.0 or (out and start < out[-1]["end"] + min_gap):
                continue
            s.update(start=round(start, 2), end=round(end, 2), enabled=True, image=None,
                     context=self.context_for(start, end, lines))
            out.append(s)
        for i, s in enumerate(out):
            s["id"] = f"s{i:02d}"
        return out

    def plan(self):
        self.ensure("cut")
        print("[장면 기획] 설명 이미지 위치 + 말로 나온 개인정보 (Claude)")
        plan = {"scenes": [], "spoken_pii": []}
        if not llm.available():
            print("  ※ API 키가 없어 건너뜀")
        else:
            lines, total = self.lines(), self.timeline.total
            kinds = self.kinds
            max_scenes = math.ceil(total / self.settings["scene_every"]) if kinds else 0
            raw = llm.plan_edit([{k: l[k] for k in ("id", "start", "end", "text")} for l in lines], total,
                                max_scenes, kinds)
            plan["scenes"] = self.clean_scenes([s for s in raw["scenes"] if s["kind"] in kinds], lines, total)
            by_id = {l["id"]: l for l in lines}
            for p in raw["spoken_pii"]:
                span = locate_phrase(by_id[p["line_id"]]["words"], p["phrase"]) if p["line_id"] in by_id else None
                if span:
                    plan["spoken_pii"].append({**p, "orig_start": span[0], "orig_end": span[1], "enabled": True})
        save_json(self.p("plan.json"), plan)
        for s in plan["scenes"]:
            kind = "오브젝트" if s["kind"] == "object" else "카드"
            print(f"  {media.fmt_time(s['start'])}~{media.fmt_time(s['end'])} {kind}: {s['idea']}"
                  + (f"  (로고: {s['logo']})" if s.get("logo") else ""))
        for p in plan["spoken_pii"]:
            print(f"  삐 처리: \"{p['phrase']}\" ({p['kind']})")

    def illustrate(self, only=None):
        self.ensure("plan")
        print("[이미지 생성]")
        engine = illustrate.resolve_engine(self.settings["image_engine"])
        if engine == "claude" and not llm.available() or engine == "openai" and not imagegen.available():
            print(f"  ※ {'Claude' if engine == 'claude' else 'OpenAI'} API 키가 없어 건너뜀")
            return
        plan = load_json(self.p("plan.json"))
        targets = plan["scenes"]
        if only:
            targets = [s for s in targets if s["id"] == only]
            for s in targets:
                s["image"] = None
        try:
            illustrate.make_illustrations(targets, self.scenes_dir, self.size, STYLE_DIR, engine,
                                          self.settings["image_quality"])
        finally:  # 일부가 실패해도 완성된 그림은 저장 (다음 실행 때 빠진 것만 다시 그림)
            save_json(self.p("plan.json"), plan)

    def scan(self):
        self.ensure("cut")
        print("[개인정보 스캔] 화면 글자 OCR")
        m = self.meta
        dets, dt = privacy.scan(self.src, (m["width"], m["height"]), self.timeline, self.settings["scan_mode"],
                                self.settings["scan_fps"])
        save_json(self.p("ocr.json"), {"dt": dt, "detections": dets})
        _, flagged = privacy.find_pii(dets, llm.available())
        save_json(self.p("pii_flagged.json"), flagged)
        for text, kind in flagged.items():
            print(f"  모자이크 대상: \"{text}\" ({kind})")
        if not flagged:
            print("  개인정보 없음")

    # ------------------------------------------------------------ 자막
    def subtitles(self):
        """{"proofread": bool, "cues": [{start, end, text}]} — 없으면 전사 결과로 만든다."""
        path = self.p("subtitles.json")
        if not path.exists():
            self.ensure("cut")
            m = self.meta
            cues = subtitles.build_cues(load_json(self.p("transcript.json")), self.timeline, m["width"] < m["height"])
            save_json(path, {"proofread": False, "cues": cues})
        return load_json(path)

    def save_subtitles(self, cues, proofread=None, translated=None):
        data = self.subtitles()
        clean = []
        for c in sorted(cues, key=lambda c: float(c["start"])):
            text = str(c["text"]).strip()
            if text:
                start, end = float(c["start"]), float(c["end"])
                cue = {"start": round(start, 2), "end": round(max(end, start + 0.2), 2), "text": text}
                if c.get("original"):
                    cue["original"] = c["original"]
                clean.append(cue)
        data["cues"] = clean
        if proofread is not None:
            data["proofread"] = proofread
        if translated is not None:
            data["translated"] = translated
        save_json(self.p("subtitles.json"), data)

    def rebuild_subtitles(self):
        self.p("subtitles.json").unlink(missing_ok=True)
        print(f"[자막] 음성 인식 결과로 다시 만들었습니다: {len(self.subtitles()['cues'])}줄")

    def proofread(self):
        translate = self.settings["translate_ko"]
        print("[자막 " + ("번역·교정] 외국어 → 한국어" if translate else "교정] 맞춤법 · 인식 오류 · 숫자 표기") + " (Claude)")
        if not llm.available():
            print("  ※ API 키가 없어 건너뜀")
            return
        data = self.subtitles()
        texts = [c["text"] for c in data["cues"]]
        fixed = []
        for i in range(0, len(texts), 150):  # 긴 영상은 나눠서 (앞뒤 문맥은 150줄 단위로 유지)
            fixed += llm.proofread(texts[i:i + 150], self.settings["vocab"], translate)
        if translate:  # 번역 후에도 외국어로 남은 줄은 앞뒤 문맥과 함께 한 번 더 번역
            left = [i for i, t in enumerate(fixed) if is_foreign(t)]
            if left:
                print(f"  외국어로 남은 {len(left)}줄을 다시 번역합니다")
                ctx = sorted({j for i in left for j in range(max(0, i - 2), min(len(fixed), i + 3))})
                again = llm.proofread([fixed[j] for j in ctx], self.settings["vocab"], True)
                for j, t in zip(ctx, again):
                    if j in left:
                        fixed[j] = t
        changed = [(a, b) for a, b in zip(texts, fixed) if a != b]
        for c, t, orig in zip(data["cues"], fixed, texts):
            c["text"] = t
            if t != orig:
                c["original"] = c.get("original") or orig  # 원문은 따로 보관 (자막 탭에서 비교용)
        self.save_subtitles(data["cues"], proofread=True, translated=translate or None)
        still = sum(is_foreign(t) for t in fixed)
        print(f"  {len(texts)}줄 중 {len(changed)}줄 {'번역·' if translate else ''}수정"
              + (f" (외국어로 남은 줄 {still}개 — 자막 탭에서 직접 고쳐 주세요)" if translate and still else ""))
        for a, b in changed[:20]:
            print(f"  · {a}  →  {b}")

    def needs_translation(self):
        """번역 설정이 켜져 있는데 아직 번역 안 된 외국어 자막이 남아 있는지."""
        if not self.settings["translate_ko"] or not self.done("cut"):
            return False
        data = self.subtitles()
        return not data.get("translated") and any(is_foreign(c["text"]) for c in data["cues"])

    def pii_settings(self):
        return load_json(self.p("pii_settings.json"), {"off_texts": []})

    def _ocr(self):
        data = load_json(self.p("ocr.json"), {"dt": 1.0, "detections": []})
        if isinstance(data, list):  # 이전 버전 형식
            data = {"dt": 1.0 / max(1, round(self.settings["scan_fps"])), "detections": data}
        return data

    def mosaic_detections(self):
        if not self.settings["mosaic"] or not self.done("scan"):
            return []
        flagged = load_json(self.p("pii_flagged.json"), {})
        off = set(self.pii_settings()["off_texts"])
        return [d for d in self._ocr()["detections"] if d["text"] in flagged and d["text"] not in off]

    def active_scenes(self, plan):
        return [s for s in plan["scenes"] if s["kind"] in self.kinds]

    def render(self):
        self.ensure("cut")
        if self.kinds:
            self.ensure("plan")
            self.ensure("illustrate")
        if self.settings["mosaic"]:
            self.ensure("scan")
        if self.needs_translation() and llm.available():  # 안전장치: 영어 자막이 남은 채로 렌더링되지 않게
            print("[자막] 아직 번역되지 않은 외국어 자막이 있어 먼저 번역합니다")
            self.proofread()
        print("[최종 렌더링]")
        st = self.settings
        plan = load_json(self.p("plan.json"), {"scenes": [], "spoken_pii": []})
        audio = media.load_audio(self.src, self.meta)
        if st["beep"]:
            audio = render.bleep(audio, [(p["orig_start"], p["orig_end"]) for p in plan["spoken_pii"]
                                         if p.get("enabled", True)], st["pii_audio"])
        cues = self.subtitles()["cues"]
        render.render(self.src, self.meta, self.timeline, audio, self.out, self.work,
                      size=self.size, speed=st["speed"],
                      scenes=self.active_scenes(plan), scenes_dir=self.scenes_dir,
                      pii_dets=self.mosaic_detections(), scan_dt=self._ocr()["dt"], face_mosaic=st["face_mosaic"],
                      cues=cues if st["subtitles"] else [], sub_style=st["sub_style"], sub_scale=st["sub_size"],
                      card_dim=st["card_dim"], card_mode=st["card_mode"], loudnorm=st["loudnorm"])
        subtitles.write_srt(cues, self.out.with_suffix(".srt"))
        print(f"  영상: {self.out}")
        print(f"  자막: {self.out.with_suffix('.srt')}")

    def run_all(self):
        """전체 실행. 컷 편집 후에는 Claude/OpenAI 작업(네트워크)과 OCR 스캔(CPU)을 동시에 진행해 시간을 줄인다."""
        t0 = time.time()
        self.ensure("cut")
        st = self.settings

        self.subtitles()  # 자막 파일을 먼저 만들어 둔다 (아래 작업들이 동시에 만들려고 하지 않게)

        def subtitle_work():  # 자막 교정·번역 (Claude)
            if llm.available() and ((st["proofread"] and not self.subtitles()["proofread"]) or self.needs_translation()):
                self.proofread()

        def visual_work():  # 장면 기획 → 그림 (Claude / OpenAI) — 자막 작업을 기다리지 않음
            if self.kinds:
                self.ensure("plan")
                self.ensure("illustrate")

        def scan_work():  # 개인정보 OCR (CPU)
            if st["mosaic"]:
                self.ensure("scan")

        # 서로 결과를 쓰지 않는 세 작업을 동시에 진행
        with ThreadPoolExecutor(3) as ex:
            futures = [ex.submit(subtitle_work), ex.submit(visual_work), ex.submit(scan_work)]
            for f in futures:
                f.result()  # 한쪽에서 오류가 나면 여기서 다시 발생
        self.render()
        print()
        print(f"전체 소요 시간: {media.fmt_time(time.time() - t0)}")

    # ------------------------------------------------------------ 웹앱용
    def preview(self, scene_id):
        """장면 중간 시점의 합성 프레임(JPEG)."""
        plan = load_json(self.p("plan.json"))
        s = next(x for x in plan["scenes"] if x["id"] == scene_id)
        t_new = s["start"] + min(1.2, (s["end"] - s["start"]) / 2)
        t_orig = self.timeline.to_orig(t_new)
        w, h = self.size
        png = subprocess.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{t_orig:.3f}",
                              "-i", str(self.src), "-vf", f"scale={w}:{h}", "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        k = w / self.meta["width"]
        for d in self.mosaic_detections():
            if abs(d["t"] - t_orig) <= self._ocr()["dt"]:
                media.pixelate(frame, [int(v * k) for v in d["box"]])
        if self.settings["face_mosaic"]:
            frame = render.FaceMosaic(self.size).apply(frame)
        subs = None
        if self.settings["subtitles"]:
            subs = subtitles.SubtitleOverlay(self.subtitles()["cues"], self.size, self.settings["sub_style"],
                                             self.settings["sub_size"])
        overlay = render.SceneOverlay([{**s, "enabled": True}], self.scenes_dir, self.size, self.settings["card_dim"],
                                      subs.top if subs else None, self.settings["card_mode"])
        frame = overlay.apply(frame, t_new)
        if subs:
            frame = subs.apply(frame, t_new)
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()

    def state(self):
        cut = load_json(self.p("cutplan.json"))
        plan = load_json(self.p("plan.json"))
        flagged = load_json(self.p("pii_flagged.json"), {})
        off = set(self.pii_settings()["off_texts"])
        w, h, fps = self.out_format
        info = {"name": self.name, "settings": self.settings, "llm": llm.available(),
                "openai": imagegen.available(), "out_format": f"{w}x{h} · {fps}fps",
                "image_engine": illustrate.resolve_engine(self.settings["image_engine"]),
                "status": {s: self.done(s) for s in STEPS},
                "meta": self.meta,
                "scenes": plan["scenes"] if plan else [],
                "spoken_pii": plan["spoken_pii"] if plan else [],
                "mosaic": [{"text": t, "kind": k, "enabled": t not in off} for t, k in flagged.items()],
                "output": self.out.name if self.out.exists() else None,
                "output_path": str(self.out),
                "subtitles": self.subtitles() if cut else None}
        if cut:
            tl = self.timeline
            info["cut"] = {"fillers": len(cut["fillers"]), "cuts": max(0, len(tl.segs) - 1),
                           "before": cut["duration"], "after": tl.total}
        return info

    def update_scenes(self, scenes):
        """웹 편집 내용 저장. 종류가 바뀐 장면은 그림을 다시 그려야 하므로 이미지를 지운다."""
        plan = load_json(self.p("plan.json"), {"scenes": [], "spoken_pii": []})
        old = {s["id"]: s for s in plan["scenes"]}
        lines, out = self.lines(), []
        ids = [int(s["id"][1:]) for s in plan["scenes"] if s["id"][1:].isdigit()]
        next_id = max(ids, default=-1) + 1
        for s in scenes:
            if not s.get("id"):
                s["id"], next_id = f"s{next_id:02d}", next_id + 1
                s.setdefault("visual", s.get("idea", ""))
            prev = old.get(s["id"])
            if prev:
                changed_art = any(prev.get(k) != s.get(k) for k in ("kind", "idea", "visual", "caption"))
                user_img = (prev.get("image") or "").endswith("_user.png")  # 직접 올린 그림은 유지
                s["image"] = None if changed_art and not user_img else prev.get("image")
            else:
                s["image"] = None
            s["start"], s["end"] = float(s["start"]), float(s["end"])
            s.setdefault("labels", [])
            s.setdefault("logo", "")
            s["context"] = self.context_for(s["start"], s["end"], lines)
            out.append(s)
        plan["scenes"] = sorted(out, key=lambda s: s["start"])
        save_json(self.p("plan.json"), plan)

    def update_pii(self, off_texts, spoken_enabled):
        save_json(self.p("pii_settings.json"), {"off_texts": off_texts})
        plan = load_json(self.p("plan.json"))
        if plan:
            for p, en in zip(plan["spoken_pii"], spoken_enabled):
                p["enabled"] = bool(en)
            save_json(self.p("plan.json"), plan)

    def replace_image(self, scene_id, data):
        plan = load_json(self.p("plan.json"))
        s = next(x for x in plan["scenes"] if x["id"] == scene_id)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError("이미지를 읽을 수 없습니다")
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        name = f"{scene_id}_user.png"
        ok, buf = cv2.imencode(".png", img)
        (self.scenes_dir / name).write_bytes(buf.tobytes())
        s["image"] = name
        save_json(self.p("plan.json"), plan)
