# Расшифровка записей — local transcription for macOS

Turn a recording of a meeting, call or voice note into a timestamped transcript
with speaker labels, a summary, a brief, action items and decisions — entirely on
your own Mac.

**Nothing leaves your computer.** Speech recognition runs on Whisper, speaker
separation on sherpa-onnx, and the summary on a language model you plug in
yourself. The internet is needed once, to download the models.

[![CI](https://github.com/Darkheart06/video-to-text/actions/workflows/ci.yml/badge.svg)](https://github.com/Darkheart06/video-to-text/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: macOS](https://img.shields.io/badge/platform-macOS%2013%2B-lightgrey)

🇷🇺 [Читать по-русски](README.ru.md)

![The app: a live call being transcribed, the archive on the left, a finished meeting below](docs/screenshots/main.png)

## What it does

- **Transcribes** video and audio — `mp4`, `mov`, `webm`, `mkv`, `avi`, `m4v`,
  `mp3`, `m4a`, `wav`, `flac`, `ogg`, `opus`, `aac`.
- **Tells voices apart** and lets you rename speakers; the labels are rewritten
  into every output file.
- **Records live calls** through ScreenCaptureKit — your microphone and the
  system audio as two separate tracks — transcribing as the conversation goes and
  writing running notes.
- **Summarises** into whatever you need: a meeting brief, a cost estimate with a
  recalculated total, a structured voice note, a user-interview digest, or your
  own rules written in plain language.
- **Keeps an archive** of everything already processed, searchable by title and
  by the text inside the transcripts.
- **Does the arithmetic itself.** Language models are confidently wrong at
  multiplication, so tables are recomputed in Python and exported as CSV.

## Requirements

| | | |
|---|---|---|
| macOS 13+ | Apple Silicon strongly preferred | Intel works, slower |
| Python 3.9+ | usually already installed | `brew install python@3.12` |
| ffmpeg | to pull audio out of video | `brew install ffmpeg` |
| A language model | for summaries only | [see below](#connecting-a-language-model) |

Without a language model the app still works — it just does not summarise.

## Install

```bash
git clone https://github.com/Darkheart06/video-to-text.git
cd video-to-text
bash install.sh
```

The script walks through the steps and asks before every large download: it
checks Python and ffmpeg, creates a `.venv`, installs the libraries, fetches the
speaker-separation models, helps you set up a language model and builds
`Расшифровка.app`.

### Giving it to someone without a terminal

```bash
bash packaging/make-dmg.sh
```

`dist/` gets a small `.dmg` containing an installer app that downloads Python,
ffmpeg and the models by itself and puts the application into `/Applications`.
The image is signed with a self-signed certificate, so on another Mac it has to
be opened once with **right click → Open**.

## Connecting a language model

Three ways, switched under **Настройки → Как подключена модель**. The
“Проверить связь” button really calls the model and shows its answer.

**1. Ollama** — the path of least resistance:

```bash
brew install --cask ollama
ollama pull gemma4:12b-mlx
```

Nothing else to configure: the app finds Ollama on `127.0.0.1:11434` and picks
the best installed model.

**2. A `.gguf` file on disk** — point the app at the file, no server involved.
Needs `llama-cpp-python`, which `install.sh` offers to build:

```bash
CMAKE_ARGS="-DGGML_METAL=on" .venv/bin/pip install llama-cpp-python
```

**3. Any OpenAI-compatible server** — LM Studio, `llama-server`, Jan, LocalAI.
Give it the base URL (`http://127.0.0.1:1234/v1`), the model name and a key if
one is required. The same path works with a cloud provider, if you knowingly
want to send transcripts out.

### Choosing a model for your memory

The model has to fit in RAM with three or four gigabytes to spare for the system
and the app itself.

| Mac memory | Sensible choice | Size |
|---|---|---|
| 16 GB | `gemma4:e4b` | ~10 GB |
| 24 GB | `gemma4:12b` | ~8 GB |
| 32–48 GB | `gemma4:12b`, `gemma4:26b` if you have room | 8 / 19 GB |
| 64 GB+ | `gemma4:26b` or `gemma4:31b` | 19 / 20 GB |

On Apple Silicon, models with an `-mlx` suffix are built for Metal and are
noticeably faster.

**Count occupied memory, not file size.** At a 32K context `gemma4:12b-mlx`
occupies **16 GB** while weighing 7.7 GB — the rest is the context cache. On a
24 GB Mac that is already tight, and a 27B model does not fit at all, whatever
the reviews about 24 GB graphics cards say.

**Measured on a real call** (51 minutes, 40 000 characters of transcript,
M4 Pro / 24 GB, `tools/modeltest.py`):

| Model | Memory | Time | Result |
|---|---|---|---|
| `gemma4:12b-mlx` | 16 GB | 186 s | even summary, but deadlines in tasks mostly “—” |
| `qwen3.5:9b-mlx` | 9.3 GB | 182 s | a quarter more detail, picked up deadlines and conditions |

For 24 GB: `qwen3.5:9b-mlx` — same speed, more specifics, seven gigabytes less
memory. Check it on your own recording:

```bash
python tools/modeltest.py "~/Documents/Расшифровка записей/Созвон.result.json" \
    gemma4:12b-mlx qwen3.5:9b-mlx
```

## Using it

Drop a file into the window, or press **Выбрать запись**. Pick at the top what
should come out of the recording, and watch the stages go by; at the end you get
tabs with the sections of the chosen profile plus the transcript.

### Profiles

| Profile | What you get |
|---|---|
| **Встреча или созвон** | summary, brief, decisions, tasks, risks |
| **Смета по надиктованному** | a table of work items with rates and a total |
| **Голосовая заметка** | thoughts by topic, tasks, numbers that were said |
| **Интервью с пользователем** | pains, verbatim quotes, conclusions |
| **Свои правила** | whatever you describe in your own words |

For the estimate, just dictate it: “demolition — twelve hours at three thousand,
wall chasing — eight hours at twenty-five hundred”. The model only sorts that
into columns; the multiplication and the total are done in Python, and a
`.таблицы.csv` lands next to the summary.

With **Свои правила** you write the instructions in one field and a document
template in the other. Every `##` heading in the template becomes a tab in the
window and a section in the file.

![The estimate profile: a table of work items with a computed total](docs/screenshots/estimate.png)

### Recording a call

Press **Начать запись созвона** (the app also offers this by itself when it
notices the microphone is busy). macOS will ask for Screen Recording permission —
that is what gives access to the system audio; the button in the app opens the
right settings pane directly.

Your microphone and the system output are captured as two separate tracks, so
“me” and “them” never get confused with each other. While the call is running,
everyone on the other side is simply “Собеседник”; when you stop, the system
track is separated by voice and the finished transcript has “Собеседник 1”,
“Собеседник 2” and so on.

### Naming voices while you record

A row of participants appears above the live transcript. Type names into the
“+ имя” field, then mark who is speaking with one click — press a name to tag the
latest line, or click a line and pick the person. Keys 1–9 do the same without
reaching for the mouse.

One tagged line per person is enough. When the recording stops, the app compares
the tagged speech against everything else and labels the whole transcript with
real names instead of “Спикер 2”.

The comparison runs per line rather than against an averaged cluster voice, and
what decides it is the gap between the best and second-best match, not the
absolute similarity: on a real recording a speech fragment matched its own
cluster's average at 0.52 while a different person scored 0.40. No gap, no name —
“Спикер 2” beats someone else's name on someone else's words.

### Recording a room meeting

“Записать встречу” sits next to the call button. It captures the microphone only
and needs no Screen Recording permission — around a table every voice reaches the
same microphone anyway. When it stops, the recording is separated by voice like
any other file, and names tagged during the meeting are applied throughout.

### Editing the summary

Models sometimes drag in things that were not discussed, or an action item nobody
assigned. The **«Править»** button above the text turns on pointwise editing:
a cross removes a bullet or a table row, and the text itself is editable in place.
Saving rewrites the summary and the tables, and totals in estimates are
recalculated.

**The transcript is never touched.** What was said stays as it was said — only the
derived document changes.

### Recording titles

Recordings are named after what was discussed rather than «Созвон 2026-08-27
13-32». The same model that writes the summary suggests the title, and it goes in
front of the timestamp — «Логика геймификации 2026-08-27 13-32» — so recordings
still sort by time. If the model does not answer, the old name stays.

### Archive

The panel on the left lists everything already processed — files and recorded
calls alike. Click one and it opens back into the same tabbed view. The search
box matches titles, and from three letters up it also searches inside the
transcripts. There is no separate database: the list is built from the
`.result.json` files, so it survives a reinstall.

### Command line

```bash
./run.sh meeting.mp4                          # defaults
./run.sh *.mov --speakers 3                   # you know there are three
./run.sh lecture.m4a --no-speakers            # single voice
./run.sh estimate.m4a --preset estimate       # work items with a total
./run.sh note.m4a --preset note
./run.sh --check-llm                          # test the model connection
./run.sh meeting.mp4 --gguf ~/Models/gemma-4-12b-Q4_K_M.gguf
```

## Output

For `meeting.mp4`, in `~/Documents/Расшифровка записей`:

| File | Contents |
|---|---|
| `meeting.summary.md` | the sections of the chosen profile |
| `meeting.таблицы.csv` | recomputed tables, when there were any |
| `meeting.transcript.md` | transcript with timestamps and speakers |
| `meeting.transcript.txt` | the same as plain text |
| `meeting.subtitles.srt` | subtitles |
| `meeting.result.json` | everything together — also what the archive reads |

## Configuration

The **Настройки** button, or `config.json` next to the project.

- **Язык** — `auto` guesses; naming the language is faster and more accurate.
- **Модель Whisper** — `large-v3-turbo` is the sensible default.
- **Сколько спикеров** — `0` detects automatically; an exact number is cleaner
  when you know it.
- **Порог разделения** — lower means more speakers. After separation the app
  compares the voices against each other anyway and merges clusters that turned
  out to be the same person (`speaker_merge_similarity`, `0.78` by default;
  `1.0` disables it). On a real half-hour meeting this took 28 “speakers” down
  to seven.
- **Размер контекста** — how much text the model holds at once.

## How long it takes

For an hour of audio on Apple Silicon: 3–8 minutes of recognition with
`large-v3-turbo` through MLX, 1–3 minutes of speaker separation, 1–4 minutes of
summarising. The first run is slower — the Whisper model (~1.5 GB) is downloaded
once into `~/.cache/huggingface`.

## How it works

```
file → ffmpeg (mono 16 kHz) → Whisper       (text + word timings)
                            → sherpa-onnx   (who spoke when)
                            → merge by word (turns)
                            → language model (map-reduce → final document)
                            → .md / .csv / .txt / .srt / .json
```

[docs/architecture.md](docs/architecture.md) explains the decisions behind that
line — why sherpa-onnx and not pyannote.audio, why the summary is markdown and
not JSON, why arithmetic never touches the model, and what was measured on real
recordings.

## Development

```bash
python tools/selftest.py sample.mp4   # whole pipeline, stubbed ASR and LLM
python tools/uicheck.py               # interface in a headless browser
ruff check .
```

`tools/selftest.py` runs the real speaker separation and fakes only Whisper and
Ollama, so it is fast and still catches real breakage. `tools/uicheck.py` drives
the interface with Playwright in both light and dark themes and writes
screenshots.

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — use it, change it, ship it, for free.
