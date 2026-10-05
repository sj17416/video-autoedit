"""1차 컷 편집 계획: 무음 구간 + 필러워드("어", "음") 제거.

이해를 해치지 않기 위한 안전장치:
- 단어 앞뒤에 여유(padding)를 둬서 말끝/말머리가 잘리지 않게 한다.
- 짧은 쉼(max_pause 이하)은 그대로 둬서 말의 리듬을 살린다. 긴 쉼도 완전히 붙이지 않고 keep_pause만큼 남긴다.
- Whisper가 받아적지 못한 말소리(음량이 큰 구간)는 지우지 않고 살린다.
- 필러는 단어 전체가 필러일 때만 지운다("어떻게"의 "어"는 건드리지 않음).
"""
import bisect
import re

import numpy as np

FILLERS = {"어", "어어", "어어어", "음", "음음", "으음", "음음음", "으", "으으", "흠", "엄", "어엄", "어음", "에", "에에", "아아", "그어", "저어"}
FILLER_CHARS = set("어음으엄흠")


def norm(word):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", word)


def is_filler(word):
    n = norm(word)
    return bool(n) and (n in FILLERS or (len(n) <= 4 and set(n) <= FILLER_CHARS))


def load_words(transcript):
    """환각 의심 세그먼트를 거르고, 시간순 단어 목록(세그먼트 번호 포함)을 만든다."""
    words = []
    for si, seg in enumerate(transcript["segments"]):
        if seg["no_speech_prob"] > 0.6 and seg["avg_logprob"] < -1.0:
            continue
        for w in seg["words"]:
            if not w["word"].strip():
                continue
            words.append({**w, "seg": si, "end": max(w["end"], w["start"] + 0.05), "filler": is_filler(w["word"])})
    words.sort(key=lambda w: w["start"])
    return words


def _voiced_runs(audio16k, sr, lo, hi, thr, min_len):
    a, b = int(lo * sr), int(hi * sr)
    hop = int(0.02 * sr)
    chunk = audio16k[a:b]
    if len(chunk) < hop:
        return []
    n = len(chunk) // hop
    db = 20 * np.log10(np.sqrt((chunk[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    runs, start = [], None
    for i, v in enumerate(np.append(db > thr, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if (i - start) * 0.02 >= min_len:
                runs.append((lo + start * 0.02, lo + i * 0.02))
            start = None
    return runs


def build_plan(transcript, audio16k, sr, duration, *, pad_before=0.10, pad_after=0.15,
               max_pause=0.45, keep_pause=0.25, rescue_min=0.8):
    words = load_words(transcript)
    fillers = [(w["start"], w["end"]) for w in words if w["filler"]]

    def fillers_in(lo, hi):
        return [f for f in fillers if f[0] < hi and f[1] > lo]

    # 1) 실제 말(필러 제외) 단어 구간 + 여유. 여유가 이웃 필러를 침범하지 않게 자른다.
    spans = []
    for k, w in enumerate(words):
        if w["filler"]:
            continue
        lo, hi = w["start"] - pad_before, w["end"] + pad_after
        if k > 0 and words[k - 1]["filler"]:
            lo = max(lo, words[k - 1]["end"])
        if k + 1 < len(words) and words[k + 1]["filler"]:
            hi = min(hi, words[k + 1]["start"])
        spans.append([max(0.0, lo), min(duration, max(hi, lo + 0.05))])

    # 2) Whisper가 놓친 말소리 살리기 (필러 구간은 제외)
    hop = int(0.02 * sr)
    n = len(audio16k) // hop
    db_all = 20 * np.log10(np.sqrt((audio16k[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    floor, loud = np.percentile(db_all, 10), np.percentile(db_all, 90)
    thr = floor + 0.5 * (loud - floor)
    spans.sort()
    rescued = []
    edges = [(0.0, spans[0][0] if spans else duration)] + \
            [(spans[i][1], spans[i + 1][0]) for i in range(len(spans) - 1)] + \
            ([(spans[-1][1], duration)] if spans else [])
    for lo, hi in edges:
        if hi - lo < rescue_min:
            continue
        cursor = lo
        for f0, f1 in fillers_in(lo, hi) + [(hi, hi)]:
            for r0, r1 in _voiced_runs(audio16k, sr, cursor, min(f0, hi), thr, rescue_min):
                rescued.append([max(0.0, r0 - 0.1), min(duration, r1 + 0.1)])
            cursor = max(cursor, f1)
    spans = sorted(spans + rescued)

    # 3) 병합: 짧은 쉼은 유지(단, 사이에 필러가 있으면 병합하지 않음)
    keep = []
    for s in spans:
        if keep and (s[0] <= keep[-1][1] or (s[0] - keep[-1][1] <= max_pause and not fillers_in(keep[-1][1], s[0]))):
            keep[-1][1] = max(keep[-1][1], s[1])
        else:
            keep.append(list(s))

    # 4) 긴 쉼은 완전히 붙이지 않고 keep_pause만큼 남겨 호흡을 살린다 (필러 침범 금지)
    for a, b in zip(keep, keep[1:]):
        gap_lo, gap_hi = a[1], b[0]
        fs = fillers_in(gap_lo, gap_hi)
        room_a = (fs[0][0] if fs else gap_hi) - gap_lo
        room_b = gap_hi - (fs[-1][1] if fs else gap_lo)
        a[1] += max(0.0, min(keep_pause / 2, room_a / 2))
        b[0] -= max(0.0, min(keep_pause / 2, room_b / 2))

    kept = sum(e - s for s, e in keep)
    return {
        "keep": [[round(s, 3), round(e, 3)] for s, e in keep],
        "fillers": [[round(s, 3), round(e, 3)] for s, e in fillers],
        "rescued": len(rescued),
        "duration": duration,
        "kept_duration": kept,
    }


class Timeline:
    """원본 시간 ↔ 편집본 시간 변환 (프레임 단위로 양자화)."""

    def __init__(self, keep, fps, grid=None):
        """grid: 컷 지점을 맞출 시간 격자(fps의 약수). 출력 fps가 달라도 컷 시간이 같게 유지된다."""
        self.fps = fps
        grid = grid or fps
        k = fps // grid
        segs = []
        for s, e in keep:
            fs, fe = round(s * grid) * k, round(e * grid) * k
            if fe <= fs:
                continue
            if segs and fs <= segs[-1][1]:
                segs[-1][1] = max(segs[-1][1], fe)
            else:
                segs.append([fs, fe])
        self.segs = segs
        self.offsets, off = [], 0
        for fs, fe in segs:
            self.offsets.append(off)
            off += fe - fs
        self.total_frames = off
        self.starts = [s[0] for s in segs]

    @property
    def total(self):
        return self.total_frames / self.fps

    def to_new(self, t):
        """원본 시각 → 편집본 시각. 잘려나간 구간이면 다음 컷 시작점으로 붙는다."""
        f = t * self.fps
        i = bisect.bisect_right(self.starts, f) - 1
        if i < 0:
            return 0.0
        fs, fe = self.segs[i]
        if f < fe:
            return (self.offsets[i] + f - fs) / self.fps
        return (self.offsets[i] + fe - fs) / self.fps

    def to_orig(self, t_new):
        """편집본 시각 → 원본 시각."""
        f = t_new * self.fps
        i = max(0, bisect.bisect_right(self.offsets, f) - 1)
        if not self.segs:
            return 0.0
        fs, fe = self.segs[i]
        return min(fs + f - self.offsets[i], fe - 1) / self.fps

    def kept(self, frame_idx):
        i = bisect.bisect_right(self.starts, frame_idx) - 1
        return i >= 0 and frame_idx < self.segs[i][1]
