"""ffmpeg 기반 입출력 도구: 정보 조회, 오디오 디코딩, 프레임 단위 읽기/쓰기."""
import io
import itertools
import queue
import re
import subprocess
import threading
import wave
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SR = 48000                      # 최종 오디오 샘플레이트
STD_FPS = (24, 25, 30, 50, 60)  # 48000으로 나누어떨어지는 fps만 사용 → 컷 지점 오디오/비디오 싱크가 샘플 단위로 정확
EVEN_SCALE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def run(args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def probe(path):
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    info = p.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    if not m:
        raise RuntimeError(f"영상 정보를 읽을 수 없습니다: {path}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    vline = next((l for l in info.splitlines() if "Video:" in l), "")
    fm = re.search(r"([\d.]+) fps", vline) or re.search(r"([\d.]+) tbr", vline)
    src_fps = float(fm[1]) if fm else 30.0
    fps = min(STD_FPS, key=lambda f: abs(f - min(src_fps, 60)))
    # 회전 메타데이터(세로 촬영 폰 영상)까지 반영된 실제 크기를 얻기 위해 첫 프레임을 디코딩
    png = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", EVEN_SCALE,
                          "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, check=True).stdout
    h, w = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).shape[:2]
    return {"duration": duration, "fps": fps, "src_fps": src_fps, "width": w, "height": h,
            "has_audio": "Audio:" in info}


def extract_wav16k(src, out):
    run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", out])


def read_wav_mono(path):
    with wave.open(str(path)) as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768, wf.getframerate()


def load_audio(src, meta):
    """원본 오디오를 48kHz 스테레오 int16 배열로."""
    if not meta["has_audio"]:
        return np.zeros((int(meta["duration"] * SR), 2), np.int16)
    raw = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
                          "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).reshape(-1, 2).copy()


def write_wav(path, audio):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(audio.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(np.ascontiguousarray(audio).tobytes())


# 속도 프리셋: 출력 해상도/프레임/인코더 설정
SPEED = {
    "fast":     {"max_side": 1920, "half_fps": True,  "preset": "veryfast", "crf": 21},  # 1080p·30fps
    "balanced": {"max_side": 1920, "half_fps": False, "preset": "faster",   "crf": 20},  # 1080p·원본 fps
    "best":     {"max_side": None, "half_fps": False, "preset": "medium",   "crf": 18},  # 원본 그대로
}


def grid_fps(fps):
    """컷 지점을 맞출 시간 격자. 60→30, 50→25 로 잡으면 원본 fps와 절반 fps 출력의 컷 시간이 완전히 같다."""
    return fps if fps <= 30 else fps // 2


def render_format(meta, speed):
    """(출력 너비, 높이, fps)."""
    cfg = SPEED.get(speed, SPEED["fast"])
    w, h = meta["width"], meta["height"]
    if cfg["max_side"] and max(w, h) > cfg["max_side"]:
        s = cfg["max_side"] / max(w, h)
        w, h = int(w * s) // 2 * 2, int(h * s) // 2 * 2
    fps = grid_fps(meta["fps"]) if cfg["half_fps"] else meta["fps"]
    return w, h, fps


def frame_select(segs):
    """남길 프레임 구간만 ffmpeg에서 골라내는 select 필터 (잘려나갈 프레임은 파이썬으로 넘기지 않음)."""
    if not segs or len(segs) > 800:
        return ""
    expr = "+".join(f"between(n,{fs},{fe - 1})" for fs, fe in segs)
    return f",select='{expr}'"


def iter_frames(src, fps, size, segs=None, prefetch=12):
    """(원본 기준 프레임 번호, BGR 프레임)을 순서대로 반환.

    segs가 주어지면 그 구간의 프레임만 디코딩해서 넘긴다. 읽기는 별도 스레드에서 미리 해 둬서
    디코딩 · 파이썬 처리 · 인코딩이 동시에 진행된다.
    """
    w, h = size
    vf = f"fps={fps},scale={w}:{h}:flags=bilinear" + (frame_select(segs) if segs else "")
    proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-threads", "0", "-i", str(src),
                             "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                            stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    n = w * h * 3
    q = queue.Queue(prefetch)
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                buf = proc.stdout.read(n)
                if len(buf) < n:
                    break
                q.put(buf)
        finally:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    if segs and frame_select(segs):
        indices = (i for fs, fe in segs for i in range(fs, fe))
    else:
        indices = itertools.count()
    finished = False
    try:
        for i in indices:
            buf = q.get()
            if buf is None:
                finished = True
                break
            yield i, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        stop.set()
        proc.kill()
        while not finished and q.get() is not None:  # reader 스레드가 끝나도록 큐를 비운다
            pass
        proc.wait()


def iter_keyframes(src, size):
    """키프레임만 빠르게 디코딩: (시각, BGR 프레임). 전체 디코딩보다 수 배 빠르다."""
    w, h = size
    proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "info", "-skip_frame", "nokey", "-i", str(src),
                             "-vf", f"scale={w}:{h},showinfo", "-fps_mode", "passthrough",
                             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=w * h * 3 * 2)
    times = queue.Queue()

    def read_err():
        for line in io.TextIOWrapper(proc.stderr, encoding="utf-8", errors="replace"):
            m = re.search(r"pts_time:\s*([\d.]+)", line)
            if m and "Parsed_showinfo" in line:
                times.put(float(m[1]))

    threading.Thread(target=read_err, daemon=True).start()
    n = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield times.get(timeout=30), np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.kill()
        proc.wait()


_amf_ok = None


def amf_available():
    """AMD 그래픽 하드웨어 인코더(AMF)를 쓸 수 있는지 한 번 시험."""
    global _amf_ok
    if _amf_ok is None:
        p = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=black:s=256x256:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"], capture_output=True)
        _amf_ok = p.returncode == 0
    return _amf_ok


def video_encoder_args(size, speed):
    cfg = SPEED.get(speed, SPEED["fast"])
    w, h = size
    if w * h > 1920 * 1088 and amf_available():  # 4K는 하드웨어 인코딩이 2배 이상 빠름 (실측 23초→10초)
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "qvbr", "-qvbr_quality_level", "23"]
    return ["-c:v", "libx264", "-preset", cfg["preset"], "-crf", str(cfg["crf"])]


class FrameWriter:
    def __init__(self, out, fps, size, audio_wav, log_path, audio_filter=None, speed="fast"):
        w, h = size
        self.log = open(log_path, "w", encoding="utf-8")
        af = ["-af", audio_filter, "-ar", str(SR)] if audio_filter else []
        self.proc = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
             "-i", str(audio_wav), "-map", "0:v", "-map", "1:a",
             *video_encoder_args(size, speed), "-pix_fmt", "yuv420p",
             *af, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(out)],
            stdin=subprocess.PIPE, stderr=self.log)

    def write(self, frame):
        self.proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait()
        self.log.close()
        if code != 0:
            raise RuntimeError(f"인코딩 실패 (로그: {self.log.name})")


def pixelate(frame, box, block=None):
    x1, y1, x2, y2 = box
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return
    h, w = roi.shape[:2]
    block = block or max(8, min(h, w) // 3)
    small = cv2.resize(roi, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR)
    frame[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def fmt_time(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def ensure_dir(p):
    Path(p).mkdir(parents=True, exist_ok=True)
    return Path(p)
