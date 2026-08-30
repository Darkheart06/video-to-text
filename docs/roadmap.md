# Where this goes next

Two different questions that are easy to conflate:

1. **Development** — what the app has to learn to do to be worth opening daily.
2. **Scaling** — what breaks when there are a thousand recordings and a hundred
   users, and what to do about it in advance.

Within each section the order is by usefulness per unit of work, not by how
interesting the idea is. Русская версия: [roadmap.ru.md](roadmap.ru.md).

## Where we are

Working and measured on real recordings:

| | State | Measured by |
|---|---|---|
| Transcription | reliable, 3–8 min per hour of audio | daily use |
| Speaker separation | 28 “speakers” → 7 on a real meeting | `tools/speakertest.py` |
| Voices during recording | numbered live, named with one click | `selftest`, section 10 |
| Familiar voices | remembered on command, recognised on their own | `selftest`, section 17 |
| Summary completeness | 15 of 27 spoken facts | `tools/modeltest.py` |
| Deadlines as dates | “tomorrow” → “tomorrow (28 August)” | `selftest`, section 13 |
| Two languages | window and documents set separately | `selftest`, section 16 |
| Editing the summary | pointwise, transcript untouched | `uicheck` |
| Archive | search by title and by transcript text | `uicheck` |

What we do **not** know, and should learn before building further:

- How many speakers there really are in your own recordings — no ground truth
  exists for any of them, so the accuracy of the count is known only on a public
  sample.
- How long editing a summary takes after each recording. That is the real
  usefulness metric: ten minutes of fixing every time means the model or the
  profile is wrong.
- How well names, terms and company names are recognised. An error here costs
  more than any other: a wrong name in a task is worse than a missing task.

## Next: a week or two

**1. A dictionary of names and terms.** Whisper accepts a prompt with expected
words (`initial_prompt`), and that is the cheapest way to improve exactly where
quality sags now: surnames, product names, internal jargon. The list lives in
settings and feeds both recognition and the model prompt. A day of work, visible
immediately.

**2. Ground truth for speakers.** Take three or four recordings where the number
of participants is known exactly, run `tools/speakertest.py --list`, and set the
threshold from that instead of from a public sample. It is the only place left
where we tune blind.

**3. The threshold for recognising familiar voices.** Memory between recordings
works, but its threshold (0.65 with a 0.05 margin) was chosen from first
principles, not measured: inside one recording the microphone and the room are
the same, between recordings they are not, and how close a voice is to itself a
week later has never been measured here. Two or three pairs of recordings with
the same people would settle it.

**4. The two call tracks, kept apart.** Measurement showed that on a call none
of five print models tells one person from another (the gap is inside the noise),
and the model is not the reason — what gets saved is a mix of two tracks, and the
far end has already been through the platform's noise suppression. The first step
is keeping the tracks separate (at least under `keep_wav`) and measuring quality
on the system one: without that, any discussion of call voices is guesswork. See
[architecture.md](architecture.md).

**5. An edit metric.** The app already records that a summary was edited
(`summary["edited"]`). Showing it in the archive and counting the share of
edited recordings makes it visible whether a profile works, and gives model
comparisons a target beyond taste.

**6. Export to .docx and .pdf.** Summaries usually travel onwards — into email,
into tasks, to a client. Right now they are copied out of the window by hand.

## Medium term: a month or two

**Screen recording and markers on the video.** Only audio is captured today,
while people share their screen constantly: a mockup, a spreadsheet, a bug in
the interface. Recording the whole screen — or a single app — gives that
context, and the transcript gives it navigation: markers on the timeline where a
decision, a task or a figure was said, and a click to jump there, the way
chapters work on YouTube. Technically ScreenCaptureKit already captures the
audio and can capture video, including a single window; the markers come from
the finished summary, since every item knows the line it was built from. The
hard part is not the capture — it is file size and a player inside the window.

**Profiles for jobs, not for genres.** Today a profile is “meeting”, “estimate”,
“interview”. More useful would be “client call about a building site”, “weekly
planning”, “candidate screening” — with the fields those actually need. The
machinery exists (“your own rules”); what is missing is ready-made sets and
picking a profile automatically from the recording's title.

**Ask the archive.** “What did we decide about onboarding last month?” — text
search cannot answer that, a model reading several transcripts can. Technically:
an index over paragraphs and the matches pasted into the question. Mind the
context budget: on 24 GB that is 5–10 fragments, no more.

**Calendar.** Recording starts by hand, while the calendar event already knows
the title, the participants and the time. From there: automatic start, a
participant list ready for voice tagging, and a meaningful recording title
without asking the model.

**Mixed languages.** Whisper picks a language once per chunk; on a call that
switches between Russian and English, that produces nonsense. Fixed by detecting
the language per fragment.

**Speed.** The stages run in sequence today. Separation can run in parallel with
recognition — they compete for different resources. Expected saving on an hour
of audio: a minute and a half or two.

## Scaling

### When there are many recordings

The archive reads `.result.json` files and caches by mtime. That is honest and
cannot drift out of sync with the files, but it is linear in their number.

- **Up to ~500 recordings** — fine as it is, nothing to do.
- **500–3000** — an SQLite index beside the folder: the files stay the source of
  truth, the database only makes opening and searching fast.
- **Beyond that** — full-text search (SQLite FTS5), tags and folders, moving old
  material into an archive folder.

Separately: text search currently reads whole files. On a thousand transcripts
that is noticeable, and it is the first thing that will bind.

### When there are many users

- **Signing and notarisation.** The DMG is signed with a self-signed certificate,
  so the recipient has to open it with right-click → Open. Proper distribution
  needs the Apple Developer Program ($99/year) and notarisation. This is the main
  obstacle between “my app” and “an app other people use”.
- **Updates.** Updating means `git pull` today. For other people that means
  Sparkle: check the version, download, install in one click.
- **Errors.** There is no telemetry and there should not be — the app promises
  that nothing leaves the machine. So: a “save an error report” button producing
  a local file the person sends if they choose to.
- **Installation.** `brew install --cask` removes the explanations about Python
  and ffmpeg. The formula is one file and answers half the questions.
- **Documentation.** The README is already long; a dozen users will need a short
  “getting started” page and an FAQ.

### When the hardware varies

The app assumes Apple Silicon with 24 GB. Others will have 8 GB, an Intel chip,
or 128 GB.

- Pick the model from the available memory automatically instead of advising
  through a table.
- Be able to say “on this machine the summary will take 20 minutes” before the
  run, not after.
- A job queue: several recordings started at once currently compete for memory.

### What not to do

- **Cloud by default.** The whole advantage is that the recording never leaves
  the machine. The moment there is a “send us the file, we'll be faster”, the
  reason to use it disappears.
- **A chat over the recordings instead of a document.** Tempting, but a document
  can be checked and attached to a task; a chat answer is new every time.
- **A Windows or Linux port** — until people ask for it. Half the decisions rest
  on ScreenCaptureKit and MLX, and porting them for a hypothesis is expensive.

## How to decide

The project already has a rule worth keeping: **nothing changes in speaker
separation or summarisation without a measurement before and after.** Three
tools exist for exactly that:

```bash
python tools/speakertest.py rec.m4a --truth 4     # how many voices, and how far off
python tools/modeltest.py rec.result.json qwen3.5:9b-mlx gemma4:12b-mlx
python tools/selftest.py sample.mp4               # that nothing else broke
```

Metrics worth watching:

| Metric | How | Now |
|---|---|---|
| Speaker-count accuracy | `speakertest` against the truth | unknown on own recordings |
| Summary completeness | share of spoken numbers and deadlines kept | 15 of 27 |
| Time per minute of audio | file in, files out | ~0.2× duration |
| Share of edited summaries | `summary["edited"]` across the archive | not counted |

That last row is the most important and the cheapest to build: it answers “did
this get better” without any hypothesis at all.
