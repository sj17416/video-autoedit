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


OPENAI_STT_MODEL = "gpt-4o-transcribe-diarize"


def transcribe_openai(wav_path, out_json):
    """OpenAI 전사 (gpt-4o-transcribe-diarize).

    측정해 보니 whisper-1은 한영이 섞인 대화에서 영어 구간을 통째로 빠뜨리거나 다른 언어로 오인했고,
    gpt-transcribe는 시간 정보를 주지 않았다. diarize 모델은 한국어·영어를 원래 언어 그대로 정확히 받아적고,
    말 덩어리(발화)마다 시작·끝 시간과 "Hmm", "Uh" 같은 추임새를 따로 돌려준다.
    단어별 시간은 발화 안에서 글자 수 비율로 나눠 붙인다 (컷은 발화 경계 기준이라 정확).
    """
    import openai

    from .llm import UserError

    audio, sr = read_wav_mono(wav_path)
    duration = len(audio) / sr
    mp3 = out_json.with_name("audio_stt.mp3")  # 업로드 용량을 줄이려고 mp3로 (2분 ≈ 0.7MB)
    from .media import run
    run(["-i", wav_path, "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", mp3])
    if mp3.stat().st_size > 24 * 1024 * 1024:
        raise UserError("영상이 너무 길어(약 70분 이상) OpenAI 전사 한도를 넘습니다. 옵션에서 '전사: 로컬'을 골라 주세요.")
    print(f"  OpenAI {OPENAI_STT_MODEL}로 전사 중... (영상 길이 {duration:.0f}초)")
    t0 = time.time()
    jobctl.check()
    try:
        with open(mp3, "rb") as f:
            r = openai.OpenAI(max_retries=4, timeout=600).audio.transcriptions.create(
                model=OPENAI_STT_MODEL, file=f, response_format="diarized_json", chunking_strategy="auto")
    except openai.AuthenticationError:
        raise UserError("OpenAI API 키가 올바르지 않습니다. 앱 왼쪽 '설정'에서 키를 다시 넣어 주세요.") from None
    except openai.RateLimitError:
        raise UserError("OpenAI 요청 한도 또는 크레딧 문제로 전사하지 못했습니다.") from None
    from .cutplan import is_filler
    data = {"duration": duration, "engine": OPENAI_STT_MODEL, "segments": []}
    for seg in r.segments:
        text = (seg.text or "").strip()
        tokens = text.split()
        if not tokens or seg.end <= seg.start:
            continue
        only_fillers = all(is_filler(t) for t in tokens)
        weights = [len(t) + 1 for t in tokens]
        total, acc, words = sum(weights), 0, []
        for tok, wgt in zip(tokens, weights):
            s = seg.start + (seg.end - seg.start) * acc / total
            acc += wgt
            e = seg.start + (seg.end - seg.start) * acc / total
            # 발화 전체가 추임새면 잘라도 안전, 문장 속 추임새는 위치가 대략적이라 남긴다
            words.append({"start": round(s, 3), "end": round(e, 3), "word": tok, "prob": 1.0,
                          "cuttable": only_fillers or not is_filler(tok)})
        data["segments"].append({"start": seg.start, "end": seg.end, "text": text, "speaker": seg.speaker,
                                 "avg_logprob": 0.0, "no_speech_prob": 0.0, "words": words})
    print(f"  발화 {len(data['segments'])}개, {time.time() - t0:.0f}초")
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


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
