# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [1.1.0] — 2026-08-27

Everything here came out of using the app on real calls for a day, and every
number below was measured rather than estimated.

### Added

- **Pointwise editing of the summary.** «Править» above the text turns a section
  into an editable document: a cross deletes a bullet or a table row, the text
  itself is editable in place, and saving recomputes the tables and the totals.
  The transcript is never touched — what was said stays as it was said.
- **Recording a room meeting.** «Записать встречу» captures the microphone alone
  through AVAudioEngine, without Screen Recording permission, and separates the
  voices afterwards like any other file.
- **Naming voices during the recording.** A row of participants above the live
  transcript; one click (or keys 1–9) tags who is speaking. One tagged line per
  person is enough — when the recording stops, the tagged speech is compared
  against the rest and the whole transcript gets real names.
- **Meaningful titles.** Recordings are named after what was discussed —
  «Логика геймификации 2026-08-27 13-32» instead of «Созвон 2026-08-27 13-32».
  The timestamp stays, so recordings still sort by time.
- **Real dates instead of “tomorrow”.** Deadlines in tables are resolved against
  the date of the recording and the date is appended in brackets: «завтра
  (28 августа)». Done in Python, not by the model, which is as bad at date
  arithmetic as it is at multiplication.
- **`tools/modeltest.py`** — runs several models or several settings over the
  same real transcript and scores how much of the specifics survived.
- **`tools/speakertest.py`** — counts the voices found in a recording under
  different settings and, given the true number, scores the miss.
- **`tools/recover.py`** — rebuilds the outputs of a recording that was
  interrupted, from the chunks left in the working folder.

### Changed

- **The speaker-merge threshold is now derived from the recording itself.** How
  similar two voice prints of the same person are depends on the microphone, the
  room and the connection: on one recording two fragments of the same speaker
  score 0.95, on another 0.70 — so a number fixed in settings is bound to be
  wrong somewhere. The app now splits each voice's speech in two, compares the
  halves, and uses that “how similar is a person to themselves *here*” as the
  reference. It doubles as a measure of how far voice prints can be trusted at
  all, so the threshold only ever moves up from the configured number, never
  down: on a public four-speaker sample where different people score 0.83 while
  a person matches themselves at 0.70, merging stays exactly as timid as before,
  while on a steady recording it becomes stricter than the fixed 0.78. The
  halves are dealt alternately rather than split by time, so a cluster that
  actually holds two people fails towards a stricter threshold instead of a
  looser one. `speaker_merge_auto: false` brings back the fixed number.
- **Summaries are noticeably more detailed.** Measured on a 51-minute call with
  `tools/modeltest.py`, counting how many of the numbers and deadlines that were
  said reached the summary: one big chunk — 8 of 27, chunks of 24 000 — 12,
  chunks of 12 000 — 14, chunks of 12 000 plus a “what did I miss” pass — 15,
  with task rows up from 7 to 24. This is the opposite of the expectation:
  writing notes on a fragment forces the model to extract specifics, while
  giving it everything at once lets it smooth them away. `summary_chunk_chars`
  is now 12 000, `llm_max_tokens` 6 000, and the second pass is on.
- **Model recommendation for 24 GB is now `qwen3.5:9b-mlx`.** Same 51-minute
  call, same machine: `gemma4:12b-mlx` occupies 16 GB and leaves most deadlines
  empty; `qwen3.5:9b-mlx` occupies 9.3 GB, takes the same three minutes and
  brings a quarter more specifics. Occupied memory is not file size — at a 32K
  context the 7.7 GB gemma file becomes 16 GB of RAM.

### Fixed

- **Names tagged during a call were assigned to the wrong people.** The tagged
  fragment used to be compared against the cluster's average voice, which on a
  real recording scored 0.52 for its own speaker and 0.40 for a different one —
  no gap worth deciding on. The comparison is now per line, and what decides is
  the gap between the best and the second-best match, with the whole speaker
  labelled by a majority vote weighted by how long each line was.

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
