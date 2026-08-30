# Architecture and the decisions behind it

This is not a description of what the code does — the code says that. It is a
record of *why* it does it that way, and of what was measured on real
recordings. Русская версия: [architecture.ru.md](architecture.ru.md).

## The pipeline

```
file → ffmpeg (mono 16 kHz WAV)
     → Whisper                 text + per-word timings
     → sherpa-onnx             who spoke when
     → merge by word           turns
     → filler cleanup          without the "uh"s, verbatim kept in JSON
     → language model          map-reduce → document by profile template
     → table recalculation     arithmetic in Python, not in the model
     → .md / .csv / .txt / .srt / .json
```

Live call recording bypasses the first three steps: a Swift helper captures two
audio tracks and each chunk goes straight to Whisper.

## Choices

**sherpa-onnx, not pyannote.audio.** pyannote needs a HuggingFace token and a
per-model licence acceptance. That is fine for a research script and hostile for
something you hand to a colleague as a DMG. sherpa-onnx runs the same
pyannote-segmentation-3.0 model as ONNX, plus WeSpeaker CAM++ embeddings, with
no account and no network after the first download (~35 MB).

**Whisper twice over.** mlx-whisper is several times faster on Apple Silicon but
gives no progress callback; faster-whisper is slower but reports progress and
runs on Intel. The app prefers MLX and slices the audio itself to produce a
progress bar, falling back to faster-whisper when MLX is missing.

**Cut at silence, not at the clock.** Ten-minute chunks are cut at the quietest
point within ±45 seconds of the target, otherwise a word is torn in half at
every boundary.

**Speakers are assigned per word, not per Whisper segment.** A segment where the
speaker changes mid-sentence gets split. Single words that drift to a neighbour
are pulled back (`merge._smooth`), and micro-turns wedged between two turns of
the same person are collapsed (`merge._drop_slivers`) — those are almost always
a diarization boundary that slipped, not a real interruption.

**Markdown, not JSON, from the model.** Local models emit broken JSON often
enough to matter. A fixed set of `##` headings is parsed reliably and degrades
gracefully: a malformed section is still readable text.

**`think: false` for reasoning models.** Measured on gemma4:12b-mlx: 32 seconds
and 749 tokens with reasoning enabled, 10 seconds and 204 tokens without, same
output quality. Combined with `keep_alive` and a warm-up call, a summary of a
one-minute recording went from “apparently hung” to ten seconds.

**Arithmetic never touches the model.** Ask a language model for `12 × 3000` in
the middle of a table and it will happily write `34 000`. So the model is
instructed to fill only the quantity and rate columns and to leave the cost
column empty; `app/compute.py` finds the markdown tables, recognises columns by
the meaning of their headers, multiplies, adds the total row and exports a CSV
with bare numbers that spreadsheets can sum.

**No database.** Every processed recording already leaves a `.result.json` with
everything in it. The archive is built by scanning those files and cached by
mtime, so it survives a reinstall, works for recordings made before the archive
existed, and cannot drift out of sync with the files — it *is* the files.

## Live call recording

**Two tracks instead of diarization.** The microphone and the system output are
captured separately, so “me” and “them” are distinguished by source, not by
voice. That is fundamentally more reliable — see the section below on how badly
voice clustering can behave.

**ScreenCaptureKit, not a virtual audio driver.** Apple's own API needs only the
Screen Recording permission and works on any macOS 13+. Since macOS 15 the same
stream also delivers the microphone, so both tracks come from one place.

**Raw PCM, not WAV.** The helper writes `mic.pcm` and `sys.pcm`; Python reads the
growing tail as the call proceeds. A WAV header would carry a wrong length until
the file is closed.

**The permission check lies.** `CGPreflightScreenCaptureAccess()` returned `true`
from the terminal while ScreenCaptureKit immediately failed with −3801. So there
is no pre-flight check: the app tries for real and translates the refusal into a
sentence a human can act on.

**Echo between tracks.** macOS mixes the microphone into the system stream, so
every sentence appeared twice. Duplicates are dropped by time overlap, text
similarity and relative loudness — the louder track wins.

## What voice clustering actually does

This is the part worth reading before trusting any speaker labels.

On a real half-hour meeting, sherpa-onnx with automatic speaker count produced
**28 speakers**. Pairs of those clusters scored 0.94–0.97 cosine similarity
against each other on WeSpeaker embeddings, while genuinely different people in
the same recording scored 0.0–0.7. In other words, it was splitting one person
into many, and the transcript showed the speaker changing mid-sentence.

`diarize.refine()` fixes this after the fact: a voice print is computed per
cluster and clusters the model itself considers the same voice are merged.

Three details that had to be right:

1. **Complete linkage, not centroids.** The first version averaged embedding
   vectors and merged by centroid — which chained: A similar to B, B to C, and
   twenty of the thirty minutes collapsed into a single "voice". Now two groups
   merge only if *every* member of one is similar enough to *every* member of the
   other.
2. **The "fragment" threshold scales with the recording** (`min_speaker_share`,
   1% by default). Thirty seconds of speech in an hour-long meeting is almost
   certainly a diarization artefact; in a three-minute note it is a participant.
   Fragments are given to the nearest voice instead of becoming "Speaker 6".
3. **Clusters with no usable audio** (every span under a second) get no
   embedding, so they are assigned by time adjacency instead.

Result on that meeting: 28 → 7 speakers, speaker changes down from 7.4 to 5.4
per minute. On a five-minute slice of the same recording: 8 → 3, with the most
similar remaining pair dropping from 0.97 to 0.58.

Refinement is skipped when the user states the speaker count explicitly — then
the count is theirs to decide.

### Where the threshold comes from

“Similar enough” was a number in the settings, and a number cannot cover it. How
alike two prints of the *same* person are depends on the recording as a whole:
the microphone, the room, the codec, how much each person actually says. On the
half-hour meeting above, one person's clusters matched each other at 0.94–0.97.
On sherpa-onnx's own four-speaker sample — half a minute of speech in total —
a person matches themselves at 0.68–0.78 while two *different* speakers score
0.83. The same 0.78 is far too strict in the first case and disastrous in the
second.

So the reference is measured per recording (`diarize.self_similarity`): each
cluster's speech is dealt alternately into two halves and the halves are
compared. That yields “how similar is a person to themselves here”, and the
threshold is set a margin below the lower quartile of those values.

Two safeguards, because this measurement can be wrong:

- **The threshold only moves up.** A low reference means the prints are noisy —
  exactly when merging is dangerous, as the four-speaker sample shows. Dropping
  the threshold there would merge different people, so the configured
  `speaker_merge_similarity` is a floor, not a starting point, and 0.92 is the
  ceiling.
- **Halves are dealt alternately, not split by time.** If a cluster really holds
  two people, a time split makes the halves look like different people and pulls
  the reference down; dealing them alternately puts both people in both halves
  and pulls it up — which, given the floor above, means the failure mode is a
  stricter threshold rather than a looser one.

Clusters with under 12 seconds of speech are left out of the measurement: half
of a short cluster is too little audio for a print worth comparing. When nothing
can be measured, the configured number is used unchanged.


### Voices during the recording, not after it

Separation after the fact is more accurate — it sees the whole file — but it
arrives too late to be useful while people are still talking. So the same voice
prints are computed live: each new line long enough for a print (1.5 s) is
compared against the prints kept per voice, best-of rather than an average,
because averaging smears the short lines that dominate a live conversation.

Above the merge threshold the line joins that voice; below it, a new voice
appears. The threshold sits deliberately high (0.62 by default): the two failure
modes are not symmetrical. An extra voice costs one click — name two chips the
same and they merge. Two people fused into one voice cannot be undone by any
amount of naming. Measured on sherpa-onnx's four-speaker sample, whose 2–5
second lines are the worst case for prints: 0.50 found two voices and merged
different people 22 times, 0.62 found five and merged four times.

Names given live are treated as tags, which is what the post-recording pass
already knows how to use: the whole file is separated properly when the
recording stops, and the tagged speech decides which cluster carries which name.

### Why a better print model does not rescue a call

The first instinct when voices get confused is to reach for a stronger model.
Measured on a real call (8.7 minutes, four voices found): four models from the
sherpa-onnx zoo against the current one, on non-overlapping speech, cosine
similarity.

| model | itself (halves of one utterance) | gap "same − different" |
|---|---|---|
| wespeaker en voxceleb CAM++ (current) | 0.83 | **+0.02** |
| wespeaker zh cnceleb ResNet34 | 0.82 | −0.03 |
| 3D-Speaker ERes2NetV2 | 0.69 | −0.03 |
| 3D-Speaker CAM++ zh_en advanced | 0.59 | −0.02 |
| NeMo TitaNet large | 0.50 | −0.05 |

Read it like this: within one utterance a print is stable (0.83), but across
utterances **no model tells one person from another** — the gap is inside the
noise. So the model is not the problem: the audio of a call has almost no
identity left in it. Two reasons, neither of which another model can fix: the far
end goes through the platform's noise suppression and codec, and the saved `.wav`
is a mix of two tracks, so every remote utterance carries the microphone's own
room noise underneath.

Hence the live rules: do not lean on the print alone, but on continuity (who was
speaking a moment ago), on the participant list the person types anyway, and on
their own corrections — two chips with the same name fold into one voice. The
print still counts; it just stopped being the only argument.

What is missing for an honest answer: a measurement on the **system track alone**
— which is what the live pass actually sees, while only the mix is kept on disk.
That is the first step for anyone taking on call voice quality.

### Memory between recordings, and why learning is a command

Separation inside a recording knows no names: it only sees which fragments are
one voice. The name has to be typed again every time, even though the people on
the call are the same week after week.

The memory lives in `voices.json` next to the app: a few vectors per person —
the very prints `diarize` computes, only kept. When a new recording is
processed, live or afterwards, each voice's print is compared against them, and
what decides it is the gap rather than the absolute similarity: a floor of 0.65
and a 0.05 margin over the runner-up. Two people equally close means no name at
all — “Speaker 2” beats someone else's name on someone else's words. One name
goes to one voice per recording: if two clusters look like Leonid, the closer
one takes it.

**Learning only ever runs on an explicit command** — the central decision here,
and it costs convenience. Learning automatically pays off right up until the
first mistake: a man and a woman merged once would enter the memory as one
person and poison every later recording, silently. A person presses the button
having just looked at the names in the finished transcript, so only checked
material is stored. For the same reason only real names are kept: “Speaker 2”
and “Person 1” are dropped.

Fine-tuning the model is deliberately absent. It would need hours of speech per
person and a GPU, would help where prints already cope, and would turn a model
update into the loss of the entire memory. A handful of vectors beside an
unchanged model buys the same recognition for a kilobyte per person.

## The trash and the queue: two things that must not be lost

**Our own trash, not the system one.** A deleted recording used to go to the
macOS Trash. Recoverable in theory, not in practice: one recording is six files
with different extensions, lying there among everything else. Now the whole
recording moves to `.trash/<time>-<id>/` beside the archive, with a note of where
it came from, what it was called and when it was deleted. Putting it back is a
`replace` of files to their old home, not guesswork over filenames. The 30-day
limit is checked whenever the trash is opened — no daemon, no scheduler.

**The processing queue.** Recording and processing are not equally important. A
conversation happens once; processing can wait. So `_finish` moved out of the
recording thread into a queue: the recording thread ends immediately, the session
becomes `queued`, and a worker takes them one at a time. Before each heavy stage
it asks `_hold()`: if a recording is live, it waits. The pause sits between
stages, not inside one — Whisper cannot be stopped mid-chunk, but between stages
it can, invisibly.

Hence the rule in `is_active()`: only the recording itself counts as busy. The
previous call's processing no longer locks the microphone.

## Documents attached to a recording: context, not content

Half the tasks from a call point at a document that lives elsewhere. So attached
files are copied next to the recording (`<recording>.files`) rather than kept as
a link: the original gets renamed, and the record has to stay whole a year later.

Text is extracted with what the system already has: docx, pptx and xlsx are zip
archives of xml with the tags stripped; pdf goes through `pdftotext`, or `pypdf`
if it happens to be installed. When neither works the file still sits beside the
recording and opens on click — the card says plainly that its text cannot be
read.

In the prompt the documents come **before** the transcript, under a heading that
says “this is context for the conversation, not its content”: otherwise the
model starts retelling an attached estimate in the summary of a meeting where it
was mentioned once. The file names also go into the header the map stage sees,
so the model knows documents exist even when their text is not in that chunk.

There are limits: 6 000 characters per document, 12 000 for all of them. An
hour-long call is some forty thousand characters of transcript, and the
documents must not crowd it out of the context window.

Rebuilding the summary is an explicit command, not automatic: the transcript has
not changed, only the document made from it has to. That is minutes of model
time — spending it unasked on every attached file would be rude.

## A directory of people: a name beats a print

Voice prints on calls are unreliable (see the measurement above), and the
participant list is something the person already knows. So the directory keeps
people and their teams separately from voices: a person may have no remembered
voice, and a voice without a name is not a person. The name is the link, and
that is enough.

The payoff is twofold: the “who's on the call” list is filled in with one press,
and that same list caps live voice splitting — the app will not invent more
voices than there are named people, plus one spare.

## Two traps in testing this

**Synthetic voices prove nothing.** Both a pitch-shifted TTS voice and three
different macOS `say` voices failed to be separated at all — the segmentation
model is trained on human speech and does not treat synthesised turns as speaker
changes. `tools/voicetest.py` remains in the repo as a documented dead end.

**Reconstructed conversations lie too.** Cutting a real recording into 3-second
pieces and interleaving them produced garbage results — partly because pauses
shorter than `min_duration_off` glue turns together, partly because the
"ground truth" came from the very clustering under test. Judge speaker splitting
only on real, continuous recordings.

## Testing

`tools/selftest.py` runs the entire pipeline with Whisper and Ollama stubbed
(a fake HTTP server answers like Ollama) but with **real** speaker separation, so
it is fast and still catches actual regressions: stage order, progress
monotonicity, cancellation, every output file, speaker renaming, table
recalculation, the archive, and the call speaker split.

`tools/uicheck.py` drives the interface in headless Chromium with a fake Python
bridge, in light and dark themes, asserting that controls exist, panes render,
tables have the right number of rows, text is selectable and copying puts the
transcript — not HTML — on the clipboard.

Neither needs a Mac. That is deliberate: the interesting logic lives where it can
be tested anywhere.
