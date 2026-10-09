# CLAUDE.md

This file guides Claude Code (claude.ai/code) when working in this repository.

## What this is
A local tool that auto-edits Korean YouTube videos. It has a Flask web app and a CLI. Pipeline:
`transcribe (faster-whisper) → cut (silence + filler words "어/음") → subtitle proofread/translate (Claude)
→ plan explainer inserts (Claude) → draw images (OpenAI gpt-image or Claude SVG) → PII scan (RapidOCR)
→ render (cuts + overlays + mosaic + burned-in subtitles + bleep + loudnorm)`.
Finished videos go to the folder that contains this repo (`..`, i.e. `이집트`), or to the folder set in `config.json`.
All UI text and user-facing messages are **Korean**.

## Run
- Web app: the `영상 자동 편집기.lnk` shortcut runs `launcher.pyw`. That starts `app.py` with pythonw (no console)
  on 127.0.0.1:5000 and opens an Edge `--app` window. When the code version (max mtime of `app.py`,
  `autoedit/*.py`, `web/*`) changes, the launcher restarts the stale server automatically, unless a job is running.
- Dev server on another port: `PORT=5050 python app.py --no-browser`.
- CLI: `python main.py video.mp4 [--speed fast|balanced|best] [--vocab "..."]`. CLI runs are also registered in `projects/`.
- Install: `pip install -r requirements.txt`. torch can't be installed on this PC (Windows long-path issue), so don't add deps that need it.

## Layout
- `app.py`: Flask API and a single background job runner. `print` output goes to the UI log via `LogBuffer`, and jobs can be stopped through `autoedit/jobctl.py`.
- `main.py`: CLI. `launcher.pyw`: Windows launcher. `tools/make_icon.py`: regenerates `assets/icon.ico` and `web/icon.png`.
- `autoedit/pipeline.py`: `Project` class, which owns per-step methods, caching in `projects/<id>/work/`, settings (`DEFAULTS`), the parallel `run_all`, and the reset rules (`RESETS`).
- `autoedit/media.py`: ffmpeg I/O (imageio-ffmpeg binary), speed presets, a frame reader that uses an ffmpeg `select` filter and a prefetch thread, keyframe-only decoding, and the h264_amf/libx264 encoder choice.
- `autoedit/cutplan.py`: cut planning and `Timeline` (original ↔ edited time). Cuts are quantized on a grid fps (60→30, 50→25), so 30fps and 60fps outputs share the same cut times.
- `autoedit/llm.py`: all Claude calls. It uses `claude-opus-5-5` through `client.beta.messages.stream` with the `server-side-fallback-2026-07-01` beta, `fallbacks="default"`, structured outputs via `output_config.format`, and `UserError` for messages shown to users.
- `autoedit/imagegen.py`: OpenAI images (`OPENAI_IMAGE_MODEL`, default `gpt-image-1.5`). It draws no text in the image; captions are composited with PIL.
- `autoedit/subtitles.py`, `render.py`, `privacy.py`, `illustrate.py`, `transcribe.py`, `env.py`: as named.
- `web/`: vanilla HTML/CSS/JS (neo-brutalist cream cards on navy). No build step.

## Windows / environment gotchas (learned the hard way)
- **No VC++ runtime on this PC.** The runtime DLLs were copied next to `ctranslate2.dll`. Entry points must
  `import ctranslate2` first, or cv2, pyclipper and onnxruntime fail to load.
- `cv2.imread` / `FaceDetectorYN.create(path)` fail on Korean paths. Use `np.fromfile` + `cv2.imdecode`, or buffer overloads.
- OpenCV here is 5.0, which has no Haar cascades. Face detection uses YuNet (`models/`, auto-downloaded).
- faster-whisper's PyAV decoding is broken here, so pass a numpy array (`read_wav_mono`).
- `.bat` files must be **CP949 + CRLF**, and PowerShell scripts need a **UTF-8 BOM**, or the Korean text breaks.
- `.env` may contain mistakes (`OPENAI_KEY`, padded spaces). `env.load_env()` normalizes them, so always load env through it.
- Claude Code's shell sets `ANTHROPIC_BASE_URL`. Unset it (`env -u ANTHROPIC_BASE_URL`) when testing with the user's key,
  and launch the app via `explorer.exe <lnk>` so it gets the user's real environment.
- When writing Python through a bash heredoc, `\n` escapes inside strings got mangled several times. Use the Edit/Write tools for code.

## Measured performance (Ryzen 5 5600G + Radeon iGPU, no NVIDIA)
- whisper `large-v3-turbo` takes 26s per 60s of audio, vs 89s for `medium`, and it's more accurate.
  Batched mode combined with the filler prompt hallucinates "음..." loops, so don't use it.
- Keyframe-only OCR scanning is about 6x faster than full decoding. d3d11va hwaccel decoding is *slower* here.
- `fast` preset renders at 1080p30 and 0.7x the video's duration for 4K60 sources. h264_amf is only used for outputs above 1080p.
- Images: every missing scene is drawn concurrently, up to `illustrate.MAX_PARALLEL` = 10, sharing one OpenAI client with `max_retries=6` so 429s back off.
  In `run_all`, subtitle proofread/translate, plan→illustrate, and OCR scan run as three parallel jobs.
  `image_quality` (low/medium/high) trades speed for detail.

## Transcription engines (tested on a Korean+English conversation, 2026-10-09)
- `gpt-4o-transcribe-diarize` (`diarized_json`) is the OpenAI engine in use. Its text is accurate in both languages, and it returns per-utterance
  start/end plus filler-only segments ("Hmm", "Uh"). Word times are interpolated by character count inside each utterance.
  In-sentence fillers are marked `cuttable: False` so we never cut real speech. A 2-minute video takes about 50 s.
- `whisper-1`: whole-file mode dropped the English part (3–30 s) entirely. Per-chunk mode misdetected Malay and hallucinated "um um um". Don't use it.
- `gpt-transcribe`: no `verbose_json`, so no timestamps. Not usable for cutting.
- Re-running transcribe or cut keeps `plan.json`/scenes. `_stash_scene_times`/`_remap_scene_times` move the scenes onto the new timeline through original-video time.

## Variety-show effects (`autoedit/effects.py`)
- Claude (`llm.plan_effects`) picks `pop` / `question` / `dramatic` (흑백요리사-style B&W with a band caption) / `zoom` moments from the
  final subtitles. They are saved to `effects.json` and can be edited in the 효과 tab. SFX are synthesized in numpy (`mix_sfx`).
- Layer order per frame: mosaic → face mosaic → effects base (zoom/B&W) → icons/cards → effect captions → subtitles.
- Zoom/dramatic effects are skipped while a center card is shown.

## Rules
- **Never test inside the user's `projects/<id>`.** Copy what you need into a scratch directory, or create a test
  project and delete it afterwards. Check the `project.json` name before deleting anything.
  (In an earlier session, the user's `plan.json` was overwritten by accident and had to be recovered.)
- The user may be running the app on :5000. Don't kill it while a job is running (`/api/job`).
- Keep the Korean subtitle default: speech is transcribed in its original language, and `translate_ko` has Claude translate foreign lines.
  `needs_translation()` re-runs the translation before render if English is left.
- Never commit `.env`, `projects/`, `style_refs/` content, `logs/`, `models/`, or output videos (see `.gitignore`).
