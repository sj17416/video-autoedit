"""화면 속 개인정보 탐지 (OCR + 정규식 + Claude 판정)."""
import re
import time

import cv2
import numpy as np

from . import jobctl, llm
from .media import iter_frames, iter_keyframes

PII_PATTERNS = {
    "휴대폰번호": r"01[016789][\s.\-]?\d{3,4}[\s.\-]?\d{4}",
    "전화번호": r"\b0\d{1,2}[\s.\-)]\d{3,4}[\s.\-]\d{4}\b",
    "주민등록번호": r"\d{6}\s?[\-~]\s?[1-4][\d*]{6}",
    "이메일": r"[\w.+\-]+@[\w\-]+\.[\w.\-]+",
    "카드번호": r"\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}",
    "계좌번호": r"\b\d{3,6}-\d{2,6}-\d{4,8}(-\d{1,3})?\b",
    "차량번호": r"\b\d{2,3}\s?[가-힣]\s?\d{4}\b",
    "주소": r"[가-힣]+(시|도|구|군)\s+[가-힣0-9]+(로|길|동|읍|면)\s?\d+",
    "개인정보 항목": r"(주소|성명|이름|연락처|전화|생년월일|주민번호|계좌)\s*[:：]\s*\S+",
}
_PII_RE = [(k, re.compile(p)) for k, p in PII_PATTERNS.items()]


def regex_kind(text):
    for kind, rx in _PII_RE:
        if rx.search(text):
            return kind
    return None


def _ocr_engine():
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    # 영상 자막/화면 글자는 거의 똑바로 있으므로 글자 방향 분류(cls)는 꺼서 속도를 올린다
    return RapidOCR(params={"Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5,
                            "Rec.model_type": ModelType.MOBILE, "Global.use_cls": False,
                            "Global.log_level": "error"})


def scan(src, src_size, timeline, mode="fast", scan_fps=1.0, ocr_side=960):
    """화면 글자 OCR. 컷으로 사라지는 구간은 건너뛴다.

    mode="fast": 키프레임(보통 1~3초 간격)만 디코딩 → 전체 디코딩보다 수 배 빠름.
                 키프레임 간격이 너무 길면 2초 간격 샘플링으로 보충한다.
    mode="precise": 초당 scan_fps장씩 빠짐없이 훑는다.
    반환: (detections, 모자이크를 앞뒤로 적용할 시간 폭 dt)
    """
    engine = _ocr_engine()
    w, h = src_size
    k = min(1.0, ocr_side / max(w, h))
    size = (int(w * k) // 2 * 2, int(h * k) // 2 * 2)
    total = timeline.segs[-1][1] / timeline.fps if timeline.segs else 0
    state = {"prev": None, "res": [], "ocr": 0, "frames": 0}
    t0 = time.time()

    def process(t, frame):
        jobctl.check()
        if not timeline.kept(round(t * timeline.fps)):
            return []
        state["frames"] += 1
        gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if state["prev"] is None or np.abs(gray - state["prev"]).mean() >= 2.0:
            r = engine(frame)
            state["ocr"] += 1
            res = []
            if r.boxes is not None:
                for poly, txt in zip(r.boxes, r.txts):
                    xs, ys = poly[:, 0] / k, poly[:, 1] / k  # 원본 해상도 좌표로
                    res.append({"box": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], "text": txt})
            state["prev"], state["res"] = gray, res
        print(f"\r  화면 스캔 {min(100, t / max(total, 1) * 100):5.1f}%  ({time.time() - t0:.0f}초)", end="", flush=True)
        return [{"t": round(t, 3), **d} for d in state["res"]]  # 화면 변화가 거의 없으면 직전 결과 재사용

    detections, dt = [], 1.0 / max(1, scan_fps)
    if mode == "fast":
        times = []
        for t, frame in iter_keyframes(src, size):
            times.append(t)
            detections += process(t, frame)
        gaps = np.diff(times) if len(times) > 1 else np.array([total])
        dt = float(np.percentile(gaps, 90)) if len(gaps) else 2.0
        if dt > 3.0:  # 키프레임 간격이 길면 2초 간격으로 보충
            print(f"\n  키프레임 간격이 {dt:.1f}초로 길어 2초 간격으로 다시 훑습니다")
            mode, scan_fps = "precise", 0.5
    if mode == "precise":
        detections, state["prev"] = [], None
        step = max(1, round(1 / scan_fps)) if scan_fps < 1 else 1
        fps = max(1, round(scan_fps))
        for i, frame in iter_frames(src, f"1/{step}" if scan_fps < 1 else fps, size):
            t = i * step if scan_fps < 1 else i / fps
            detections += process(t, frame)
        dt = step if scan_fps < 1 else 1.0 / fps
    skipped = state["frames"] - state["ocr"]
    print(f"\n  {state['frames']}장 확인 · OCR {state['ocr']}장"
          + (f" (화면 변화 없는 {skipped}장 건너뜀)" if skipped else "") + f" · {time.time() - t0:.0f}초")
    return detections, dt


def find_pii(detections, use_llm):
    texts = sorted({d["text"] for d in detections})
    flagged = {t: regex_kind(t) for t in texts if regex_kind(t)}
    if use_llm and texts:
        try:
            for i in llm.classify_ocr(texts):
                if 0 <= i < len(texts):
                    flagged.setdefault(texts[i], "Claude 판정")
        except Exception as e:
            print(f"  ! Claude 화면 텍스트 판정 실패, 정규식 결과만 사용: {e}")
    return [{**d, "kind": flagged[d["text"]]} for d in detections if d["text"] in flagged], flagged
