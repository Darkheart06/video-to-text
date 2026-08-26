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
