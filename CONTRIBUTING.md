# Contributing

Bug reports, ideas and pull requests are welcome. This is a personal tool that
grew into something usable, so the bar is “does it help someone transcribe their
recordings”, not “is it enterprise-grade”.

## Getting set up

```bash
git clone https://github.com/Darkheart06/video-to-text.git
cd video-to-text
bash install.sh          # creates .venv, downloads models, builds the app
```

macOS 13+ is required for the app itself. The library code (`app/`) is plain
Python and imports fine anywhere, which is what CI checks.

## Before opening a pull request

```bash
ruff check .
python tools/selftest.py some-recording.mp4
python tools/uicheck.py
```

- **`tools/selftest.py`** runs the whole pipeline with Whisper and the language
  model stubbed out, but with real speaker separation. It checks the stages,
  progress monotonicity, cancellation, every output file, speaker renaming,
  table recalculation, the archive and the call speaker split. Give it any real
  recording with a couple of voices.
- **`tools/uicheck.py`** opens the interface in a headless browser with a fake
  Python bridge, in light and dark themes, and writes screenshots to
  `/tmp/uicheck`. It fails if a control disappears or a pane renders empty.
- **`tools/voicetest.py`** exists but is a cautionary tale: macOS `say` voices
  are not separable by diarization at all. Do not judge speaker splitting by
  synthetic audio — use a real recording.

Both tools print human-readable checks and exit non-zero on failure, so they are
usable as a pre-commit habit.

## Style

- Python 3.9+, no formatter beyond `ruff` (line length 96).
- Comments and docstrings in this project are in Russian and explain **why**,
  not what — the code already says what. Please keep that habit: a comment that
  restates the line below it is noise, a comment that records a measurement or a
  trap saves the next person an hour.
- No new runtime dependencies without a reason. The whole point is that this
  runs offline on a laptop.

## What would genuinely help

- A real multi-party call recording to validate speaker splitting on the system
  track — the one thing that has not been verified on live material.
- Windows or Linux support for the file pipeline (the live-recording helper is
  macOS-only by nature).
- Export to `.docx` / `.xlsx` instead of markdown and CSV.
