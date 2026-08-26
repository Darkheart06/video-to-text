# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [1.0.0] — 2026-08-26

First public release. Everything below was built and measured on real
recordings before shipping.

### Added

- **Transcription pipeline** — ffmpeg → Whisper (mlx-whisper on Apple Silicon,
  faster-whisper as a fallback) → sherpa-onnx speaker separation → merged turns
  → language-model summary → `.md` / `.csv` / `.txt` / `.srt` / `.json`.
- **Three ways to plug in a language model** — Ollama, a local `.gguf` file
  through llama-cpp-python, or any OpenAI-compatible server.
- **Live call recording** through a Swift ScreenCaptureKit helper: the
  microphone and the system output as two separate tracks, transcribed in
  chunks as the call goes, with running notes every few minutes.
- **Profiles** — meeting, cost estimate, voice note, user interview, or your own
  rules and document template written in plain language.
- **Table recalculation in Python.** The model only sorts what was said into
  columns; quantities × rates and the total are computed in code and exported to
  CSV, because language models are confidently wrong at arithmetic.
- **Archive** of processed recordings inside the window, searchable by title and
  by transcript text, built from the `.result.json` files rather than a database.
- **Filler cleanup** — removes hesitations and verbal tics while protecting
  meaningful phrases; the verbatim text stays in `.result.json`.
- **DMG installer** that downloads Python, ffmpeg and the models by itself, so
  the recipient never opens a terminal.

### Fixed

- **Speaker separation split one person into many.** With automatic speaker
  count, sherpa-onnx found 28 “speakers” in a real half-hour meeting, with pairs
  of clusters 0.94–0.97 similar to each other — while genuinely different people
  score 0.0–0.7. Voice prints are now compared after clustering and matching
  clusters are merged with complete linkage (every member against every member,
  so similarity does not chain A→B→C into one voice). Result on that meeting:
  28 → 7 speakers, speaker changes down from 7.4 to 5.4 per minute.
- **Everyone on a call was labelled “Собеседник”.** The system track is now
  separated by voice after the call ends, producing “Собеседник 1”,
  “Собеседник 2”, …; the microphone track stays “Я” and is deliberately kept out
  of clustering.
- **Text could not be selected in the window.** pywebview defaults
  `text_select=False`, which injects `user-select: none`. Selection is enabled
  for transcript text and disabled for chrome; copying falls back through the
  clipboard API, `execCommand` and `pbcopy` so it cannot silently fail.
- **The file picker disappeared** once any card was on screen, because the drop
  zone hides behind cards — including cards opened from the archive.
- **Every line was duplicated** as both “Я” and “Собеседник”: macOS mixes the
  microphone into the system stream. Echo is now dropped by time overlap, text
  similarity and loudness.
- **The summary hung for minutes** on reasoning models. Ollama requests send
  `think: false` — measured 32 s / 749 tokens with reasoning versus 10 s / 204
  tokens without, at the same output quality.
