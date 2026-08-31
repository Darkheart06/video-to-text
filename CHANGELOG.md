# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/).

## [1.7.2] — 2026-08-30

### Fixed

- **A name put on a line during a call could vanish by the time the transcript
  was ready.** The transcript is assembled from the voice-cluster keys, and the
  name the person typed survived only if it won a whole cluster; otherwise it
  was dropped along with the marking it was made for. Now the person is asked
  first: their name takes the cluster where that person spoke most, if that
  cluster is still unnamed, and its own key otherwise. An automatic caption
  (“Speaker 2”) never beats a human; another person's name does.
- **The marks were read from the wrong recording.** `_enrolled()` looked at the
  session in progress rather than the one being processed — and processing now
  waits in a queue, so by the time it runs a new call may already be recording.

### Added

- **Any line can be given to a different person.** Click the name in the
  transcript and pick who actually said it, or type a new name. Voice splitting
  errs in small ways — two or three lines drift to a neighbour — and until now
  that could only be corrected a whole voice at a time. This matters beyond
  reading: **voice prints are learned from exactly this marking**, and learning
  someone else's audio under your name is worse than not learning at all.
- **Two voices given the same name merge into one.** They used to stay separate:
  the person appeared twice in the card, and their voice print was learned twice
  from half the lines each.

## [1.7.1] — 2026-08-30

### Changed

- **The settings window is laid out in tabs** — *General*, *Processing*,
  *Voices*, *More* — instead of one long scroll of ten sections. Everything
  people change often is in the first tab; the thresholds and intervals that
  are set once are in the last. All the fields stay in the window even when
  their tab is hidden, so *Save* still collects the whole form and nothing
  typed on another tab is lost.
- **Deleting a recording asks in a dialog**, with the recording's name and a
  line saying it goes to the trash for 30 days. The old two-step button turned
  itself into “delete?” inside a 24-pixel square — hard to read and easy to hit
  by accident.
- **The pin is a pin.** Iconsax has no pushpin at all and the nearest thing,
  a bookmark, reads as “save”, not “pin”. That one icon is drawn by hand on the
  same 24×24 grid with the same 1.5 stroke, so it does not stand out in a row
  with the rest.
- The cross next to “done” is gone. It removed the card from the working area,
  which nobody needed and everybody read as “delete the recording”; it stays
  only where it means something — on a failed or cancelled job.

### Fixed

- **Dark theme left the system controls light-themed** — black arrows in the
  selects, light scrollbars. The fix is `color-scheme`, which is how a page
  tells the browser what to paint those with; our own colour variables never
  reached them.
- The icons at the bottom of the archive were pushed outside the panel by the
  long *Open the folder* button; the button shrinks now, the icons do not.
- The credit line about the icons sat beside the buttons in the settings
  footer, stretching them; it is a line under them now.
- The hint under *Window theme* explained that Save is not needed — that is
  what an instantly applied switch already says.
- The screen-source button was a different height from the buttons beside it.
  Every small button has an explicit height now.

## [1.7.0] — 2026-08-30

### Added

- **Pinned recordings and folders.** A hundred recordings in a flat list stop
  being a list. Pin the ones you keep returning to and they stay at the top;
  group the rest into folders and the archive becomes a tree that collapses.
  **The folders are not folders on disk**: moving the files would break the
  paths already written into every `.result.json` and into the attached
  documents. A folder is a label, the tree is drawn in the window, and the
  files stay exactly where Finder expects them.
- **The title can be corrected by hand.** The app invents a title from the
  subject of the conversation and does not always get it right. The pencil in
  the card header fixes it — in the documents *and* in the file names, so the
  same recording is not called two different things in two different places.
  A pinned recording keeps its pin and its folder through the rename.
- **Picking what to record from the screen now shows pictures**, the way Zoom
  and Telemost do it. One app can have several windows open and the name alone
  does not say which one is meant; a snapshot answers immediately. On macOS 13,
  where the system has no screenshot API for this, the tiles fall back to icons
  and the choice still works.

### Changed

- **The card's actions no longer hide behind the tabs.** With seven tabs the
  row scrolled and took *Copy* and *Edit* with it — to copy a summary you first
  had to scroll. The actions now sit in their own, non-scrolling part of the
  row, as icons with tooltips.
- The cross next to “done” says what it does now (it removes the card from the
  working area, not the recording) and stands beside the rename and pin
  buttons.
- **The window theme is chosen with buttons, not a dropdown** — all three
  options visible at once, applied immediately, no *Save* needed.
- **Every field is the same height.** A `select` has its own intrinsic metrics
  and came out shorter than the input next to it; in a column of settings that
  is impossible not to see.
- The hint under *Interface language* repeated the label word for word and is
  gone.

## [1.6.3] — 2026-08-30

### Changed

- **Every icon now comes from one set.** The five icons in the window were
  drawn by hand, one at a time, and it showed: different stroke weights,
  different corner radii, a window that looked assembled from spare parts.
  They are all replaced with **Iconsax** (outline, 24×24), taken from the
  Panda Icons Library file in Figma. The typographic stand-ins went with them
  — the `×` on every remove button and the `›` on the documents accordion are
  now the set's own glyphs, so nothing is a letter pretending to be an icon.
  The paths live in one `ICONS` object, colour is inherited from the button,
  so both themes and every hover state work without a second copy of anything.
- The icons ship inside the app like the typeface does — nothing is fetched
  from a network. They are CC BY 4.0, so the credit is in Settings, in the
  README, and in `app/ui/icons/LICENSE-Iconsax.md`, which also records a
  contradiction worth knowing about before the app is ever sold.

## [1.6.2] — 2026-08-30

### Fixed

- **The screen recording had no sound.** The helper writes the picture and the
  audio separately — the picture into mp4, the audio into tracks for
  transcription — so the video came out silent, which makes a recording of a
  call close to useless. The mixed track (the same one Whisper listened to, so
  the sound and the markers are counted from one point) is now muxed into the
  same file. The picture is copied, not re-encoded, so this costs seconds.

### Added

- **Full screen for the recording.** A button next to the heading, and a
  double-click on the picture. In a window the size of a palm it is often
  impossible to make out what was being shown.
- **The player no longer disappears in silence.** If the local media server
  fails to start, the card says so and keeps the markers, instead of quietly
  dropping the whole block; the reason goes into `.work/capture.log`.

## [1.6.1] — 2026-08-30

### Fixed

- **Screen recording produced an unplayable file.** The first real recording
  came out as an mp4 that no player would open: the frames were there, the
  index was not. ScreenCaptureKit delivers frames in bursts, and two of them
  landing inside the same 1/600 of a second gave the encoder a repeated
  timestamp. That single rejected frame put the writer into a failed state,
  after which every later frame was dropped and the file was never closed —
  all of it silently, because none of the return values were checked. Frame
  timestamps are now forced to increase strictly, every rejection is reported,
  and the video is finalised before anything else during shutdown, so a slow
  `stopCapture` can no longer cost the whole recording.
- **A broken video is no longer offered as if it worked.** An mp4 without its
  index is checked for and removed instead of being listed among the files and
  loaded into the player as a black rectangle.
- **The capture helper now keeps a log** (`.work/capture.log`, local as
  everything else). Its output used to be read only when it crashed, so a
  failure in the middle of a call left no trace at all. Reading the stream
  continuously also removes the risk of the helper blocking on a full pipe.
- **Markers now match words in a different grammatical form.** A decision
  recorded as «обсуждение остановлено» was not matched to the line where
  someone said «остановить», so the marker people expected most was the one
  that went missing. Matching is done on word stems now.

## [1.6.0] — 2026-08-30

### Added

- **Screen recording — the whole screen or one app.** People share their screen
  on almost every call, and until now only the audio was kept. Pick the source
  next to the record button (audio only, the whole screen, or one of the running
  apps) and the video is captured through the same ScreenCaptureKit stream as
  the system audio: no extra permission, no virtual driver. It is written at
  8 fps and 1600 px wide — a screen share, not a film, so an hour costs hundreds
  of megabytes rather than tens of gigabytes.
- **Markers on the recording, like chapters on YouTube.** Every decision, task
  and risk in the summary gets a marker where it was actually said, and a click
  plays the recording from that moment. **The time comes from the transcript,
  not from the model**: each item is matched against the lines by wording, and
  an item with no convincing match gets no marker — an empty spot is honest, a
  marker pointing at the wrong minute is not. Markers are also exported as
  `.chapters.vtt`, which players, YouTube and editing software understand.
- **A player in the window.** The recording plays right in the card — video when
  the screen was captured, audio otherwise — with the markers on a strip above
  it and as a list under it.
- **The window theme can be chosen**, not only inherited: Settings → *Window
  theme* — follow the system, light, or dark, and the choice beats the system in
  both directions.

### Changed

- The Settings button is a gear icon instead of the word.

## [1.5.1] — 2026-08-30

### Changed

- **Pictures and documents no longer share a shelf.** Images attached to a
  recording are shown as square previews and open full-size on click; documents
  stay a list and open in whatever app owns them. **Edit the list** switches the
  block from viewing to removing, so a stray click cannot delete anything.

## [1.5.0] — 2026-08-30

### Added

- **Documents attached to a recording.** Half the tasks from a call point at
  something that lives elsewhere — an estimate, a brief, an email — and a week
  later “fix it per the comments” means nothing. Attach those files to the
  recording and their text goes to the model together with the transcript, so
  the summary uses the real names, figures and wording. The files are copied
  next to the recording, so renaming or deleting the original leaves the record
  whole. Text is pulled from `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`, `.md`,
  `.csv` and `.rtf`; anything else still sits there as a file. **Rebuild the
  summary with the documents** re-runs just the summary — the transcript is not
  touched.
- **A directory of people and teams.** Participants had to be typed in again
  before every call, though the people are the same. Settings → *People and
  teams* keeps who works where; during a call one press on a team chip fills in
  everyone from it, and the directory marks whose voice is already remembered.

## [1.4.1] — 2026-08-30

### Changed

- **One recording in the working area, not a feed of all of them.** Cards for
  every recording stacked up in the middle of the window and people lost track
  of which one they were looking at. Now the working area shows exactly the
  recording you opened — from the archive on the left, or the one you just
  started — and everything still being processed sits above it as a single
  “Working on now” line that opens on click.

## [1.4.0] — 2026-08-30

### Added

- **A trash you can actually undo.** Deleting a recording used to hand the files
  to the system Trash, where putting them back meant hunting through Finder by
  filename. Now they go to the app's own trash: the whole recording in one
  place, listed with the day it goes for good, back in the archive with one
  press. Anything untouched for 30 days is swept out on its own (`trash_days`).
  The delete control is a trash icon instead of a cross.
- **A new call no longer waits for the last one to finish.** Processing a
  recording — splitting voices, writing the summary — used to hold the recorder
  hostage: a call starting right after another could not be recorded until the
  previous one was done. Processing now runs in its own queue, steps aside the
  moment a new recording starts, and picks up where it left off afterwards.
  Waiting recordings are listed under the live card, so nothing looks lost.

### Changed

- **The window was redesigned and the type is bigger.** A calmer light-first
  surface, air and hairlines instead of boxes, one accent colour on the action.
  Everything that was hard to read grew: transcript lines 12.5 → 14 px, archive
  captions 11 → 12.5, the recording clock 19 → 23. Both themes.
- **Onest instead of the system face.** The font ships inside the app — the
  promise of working without a network covers letters too. SIL OFL, so it
  travels with the app legally; the licence is in `app/ui/fonts`.

## [1.3.1] — 2026-08-28

### Fixed

- **Tagging a line no longer throws you to the end of the call.** Every redraw
  scrolled the live transcript to the newest line, so clicking a line in the
  middle of a conversation meant scrolling back to find it again. The transcript
  now stays where you left it and only follows the newest lines when you are
  already at the bottom; a “↓ to the latest lines” button appears when you are
  not.
- **The recording clock ran at double speed.** It added the elapsed time since
  the start to a duration that already counted from the start — an hour showing
  for a half-hour call. The clock now ticks from the last update it received.

### Changed

- **One person is no longer split across three voices.** Three rules, in the
  order they help: a voice that spoke a moment ago gets a head start, because
  people do not change in the middle of a sentence; voices whose prints turn out
  to be the same person are folded together as evidence accumulates; and when
  you have listed who is on the call, the app stops inventing more voices than
  there are people (plus one spare for someone you did not list).
- **Two chips with the same name become one voice.** The hint under the voices
  said so all along; now it is true — naming a second chip “Vera” merges it into
  the Vera that already exists, and the remaining numbers close the gap instead
  of jumping from 1 to 5 to 8.

## [1.3.0] — 2026-08-28

### Added

- **The app remembers voices — but only when told to.** Open a processed
  recording, check that the names are right, press **Remember voices**, and
  those people are recognised in every later recording: their name appears in
  the live transcript the moment they speak, and in the finished transcript and
  summary without a single click. Nothing is learned automatically and on
  purpose — one splitting mistake, learned silently, would follow you forever.
- Familiar voices are listed in Settings → *Familiar voices*, each with the
  number of prints kept, and any of them can be forgotten with one click.
  Recognition can be switched off there entirely.
- Only real names are remembered: “Speaker 2” and “Participant 1” mean nothing
  and are skipped. One name goes to one voice per recording — if two voices
  look like the same person, the closer one gets the name and the other keeps
  its number.
- The same from the console: `--learn RECORDING.result.json`, `--voices`,
  `--forget NAME`.

This is not fine-tuning, and it does not need to be: no model is changed, a few
voice prints per person are stored next to it — the same vectors the app
already compares inside a recording. Fine-tuning would demand hours of speech
per person and a GPU, and would help exactly where the app already gets it
right.

### Fixed

- **A renamed speaker no longer renames other people's lines.** Typing a name
  over one line during a call moves just that line to the person who already
  carries the name; renaming the whole voice is still done on the voice chip.

## [1.2.4] — 2026-08-28

### Fixed

- **Live voices no longer take silence for a person.** A print made of silence
  looks like any other silence, and once such a “voice” appeared first,
  everything stuck to it — a man and a woman ended up as one speaker. Prints are
  now built only from fragments that actually contain speech, at a stricter
  loudness gate than the one Whisper uses: a print needs a voice, not a rustle.
- **A silent microphone held up the whole live transcript.** The call recorder
  waited for both tracks to reach the same point before transcribing, so on a
  call where you listen in headphones and say nothing, nothing was transcribed
  at all — while the other side could be heard perfectly. Now a track that
  falls more than ten seconds behind stops holding the other one up, and the
  app says which one went quiet.
- **Invented subtitles glued to real speech are trimmed, not dropped.** Whisper
  merges a pause and the sentence after it into one segment — “Продолжение
  следует... Давайте пробежимся по статусам” — so throwing the segment away
  would lose what was actually said. The credits are cut from the start, the
  word timings are cut with them, and the line keeps its real beginning.
- **Whisper's invented subtitles are filtered out.** In silence the model
  confidently writes the end credits it saw in training — “Продолжение
  следует”, “Субтитры сделал…”, “Thanks for watching” — and on a call where one
  side is quiet those add up to a conversation that never happened. Such lines
  are dropped, and only when the phrase is essentially the whole line: “thanks
  for your attention, questions at the end” is real speech and stays.

## [1.2.2] — 2026-08-28

### Fixed

- **ffmpeg went missing when the app was opened from Finder.** Launched from a
  terminal everything worked; launched the way people actually launch things,
  the indicator went red and nothing could be transcribed. macOS gives a
  Finder-launched app a short `PATH` — `/usr/bin:/bin:/usr/sbin:/sbin` — with no
  Homebrew in it. The app now looks in the usual places (`/opt/homebrew/bin`,
  `/usr/local/bin`, `/opt/local/bin`, `~/.local/bin`) as well, and the launcher
  puts Homebrew back on `PATH` for anything else it starts.

## [1.2.1] — 2026-08-27

### Fixed

- **Updating took away the screen-recording permission.** `install.sh` rebuilt
  the capture helper every time, and to macOS a rebuilt binary is a different
  program: the granted permission no longer applied. Worse, the helper lived
  outside the app bundle, so the permission list showed whoever launched it
  (`python3.12`) rather than the app — there was nothing to re-grant. Now the
  helper is copied into `Расшифровка.app/Contents/MacOS`, the bundle is
  ad-hoc signed with a stable identifier, and the build skips recompiling a
  helper whose source has not changed. The app finds it through `V2T_HELPER`,
  set by the launcher.
- The banner about the missing permission now says what to do when the app is
  absent from the list: add it with “+” and restart, because macOS applies this
  permission only on the next launch.

## [1.2.0] — 2026-08-27

Two things shipped together: the app learned English, and it stopped waiting
until the end of a recording to tell voices apart.

### Added

- **The app speaks English.** Interface, progress messages, errors, profiles,
  prompts and output files all exist in English and Russian. The two are set
  separately — window language and document language — because they answer
  different questions: who presses the buttons, and who will read the brief.
  On first launch both follow the system; **Settings → Language** switches them
  without a restart.
- **Output follows the document language.** Results land in `Transcripts` or
  «Расшифровка записей», tables are `.tables.csv` or `.таблицы.csv`, the total
  row says “Total” or «Итого», and speakers are “Speaker 2” or «Спикер 2». The
  archive reads both folders, so switching loses nothing.
- **English profiles** — meeting, estimate, voice note, interview and your own
  rules, with English headings and English instructions to the model. Section
  keys are shared between languages, so a recording made in Russian opens in an
  English window with English tabs.
- **Spoken deadlines in English** — “tomorrow”, “by Friday”, “next week”, “end
  of the month” resolve to real dates the same way the Russian ones do.
- **English filler cleanup** — “um”, “you know”, “I mean”, “sort of” and their
  neighbours are removed from transcripts, with the same protection against
  cutting meaningful phrases.
- **Voices are numbered while the recording runs.** Every line long enough to
  leave a voice print is compared against the voices heard so far and gets a
  label immediately — “Speaker 1”, “Person 2” — instead of a blank or a single
  “Them” for everyone until the end. Clicking a voice in the row above the
  transcript names it, and every line that person said, before and after, takes
  the name. Correcting a name is a click; tagging from scratch was a dozen.
- **The live threshold errs towards too many voices, not too few.** Two chips
  with the same name merge in one click, two people fused into one voice cannot
  be separated. Measured on a public four-speaker sample with 2–5 second lines:
  at 0.50 it found two voices and merged different people 22 times; at the
  default 0.62, five voices and four merges. `live_speakers: false` turns the
  whole thing off; the full separation after the recording stops is unchanged
  and still more accurate.
- **A roadmap** — [docs/roadmap.md](docs/roadmap.md) and
  [docs/roadmap.ru.md](docs/roadmap.ru.md): what comes next, what breaks at
  scale, and the three things we do not yet know about our own app.
- `tools/uicheck.py --docs` now shoots the README screenshots in both languages
  as part of the interface check, so the pictures cannot fall a version behind
  the code again — which is exactly what had happened by 1.1.0.

### Changed

- `tools/selftest.py` pins the language for its Russian fixtures and gained a
  section that exercises the English side end to end: profiles, totals, dates,
  transcript, output folder and the completeness of the phrasebook itself.
- `tools/speakertest.py` accepts any media file (not only `.wav`) and takes
  `--truth` / `--list` alongside the Russian flags.
- The command line takes `--ui-lang` and `--doc-lang`.

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
