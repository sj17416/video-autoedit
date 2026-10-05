"""faster-whisper로 단어 단위 타임스탬프 전사."""
import json
import os
import time

from . import jobctl
from .media import read_wav_mono

# Whisper는 "어", "음" 같은 말더듬을 지워버리는 경향이 있어서, 필러가 섞인 예시 문장을 프롬프트로 줘서 그대로 받아쓰게 유도한다.
FILLER_PROMPT = "음, 어, 그러니까 음... 어, 이게 뭐냐면요, 음, 어어 그래서."
# 화면에 보이는 이름 → faster-whisper 모델 이름
MODELS = {"turbo": "large-v3-turbo"}


def transcribe(wav_path, out_json, model_size="turbo", vocab="", language="ko"):
    """
    측정 결과 (Ryzen 5 5600G, 60초 음성): medium 89초 / large-v3-turbo 26초.
    turbo가 3배 이상 빠르면서 더 정확하다(영어 구간을 번역해버리지 않고, 필러도 잘 잡음).
    배치 모드는 더 빠르지만 필러 프롬프트와 함께 쓰면 "음... 음..." 반복 환각이 생겨 쓰지 않는다.
    """
    from faster_whisper import WhisperModel

    name = MODELS.get(model_size, model_size)
    print(f"  Whisper '{name}' 모델 로딩 중... (처음 실행 시 다운로드)")
    model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    # 자주 나오는 용어를 프롬프트에 넣으면 그 단어를 더 정확히 받아적는다
    prompt = FILLER_PROMPT + (f" 용어: {vocab}." if vocab.strip() else "")
    audio, _ = read_wav_mono(wav_path)  # 16kHz 배열을 직접 넘겨 PyAV 디코딩(버전 호환 문제)을 피한다
    segments, info = model.transcribe(
        audio, language=None if language == "auto" else language, word_timestamps=True, initial_prompt=prompt,
        condition_on_previous_text=False, beam_size=5, vad_filter=False,
    )
    data = {"duration": info.duration, "segments": []}
    t0 = time.time()
    for seg in segments:
        jobctl.check()
        data["segments"].append({
            "start": seg.start, "end": seg.end, "text": seg.text.strip(),
            "avg_logprob": seg.avg_logprob, "no_speech_prob": seg.no_speech_prob,
            "words": [{"start": w.start, "end": w.end, "word": w.word.strip(), "prob": w.probability}
                      for w in (seg.words or [])],
        })
        print(f"\r  전사 {seg.end / max(info.duration, 1) * 100:5.1f}%  ({time.time() - t0:.0f}초 경과)", end="", flush=True)
    print()
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data
