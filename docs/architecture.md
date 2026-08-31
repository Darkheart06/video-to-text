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

### The voice-print model is swappable — and “install the best one” is not a plan

Voice prints are the shared joint of two things: clusters of one person are
merged by them, and known voices are recognised by them. So the accuracy of the
print is the most direct lever on “this line is signed with the wrong name”. The
model is chosen by the `voice_model` setting, and every option downloads from
the open sherpa-onnx releases — no tokens, no licence acceptance
(`app/diarize.py`, `EMB_MODELS`).

A measurement on sherpa's own four-speaker sample (57 seconds, four people)
shows why “install whatever tops the tables” is a poor plan:

| model | dims | similarity between *different* people | time |
|---|---|---|---|
| CAM++ (current) | 512 | 0.02–**0.83** | 13 s |
| ERes2NetV2 | 192 | 0.13–**0.62** | 44 s |
| TitaNet-large | 192 | 0.10–**0.63** | 18 s |
| WeSpeaker ResNet293 | 256 | 0.72–**0.94** | 88 s |

ResNet293 leads the public speaker-verification tables (0.53% EER against
0.71% for CAM++) and does not work here at all: different people score 0.72–0.94
against each other, and the split collapses the whole conversation into one
voice at any threshold. The likely cause is the input features — sherpa-onnx
feeds it something other than what it was trained on. Either way, the best model
on paper was the worst one here, and only running it could show that.

The second thing the table shows: **the clustering threshold is per model.** 0.6
was tuned for CAM++; ERes2NetV2 and TitaNet find the same four voices at 0.8.
Swapping the model without moving the threshold changes two things at once and
tells you nothing about either.

So CAM++ stays the default and the choice is handed to a measurement on your own
recordings (`tools/bench.py`). Changing the model clears the voice memory: prints
taken by different models live in different spaces, and comparing them is like
comparing height with weight. `voices.json` records which model took the prints,
and on a mismatch the memory is silently treated as empty.

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

## Markers and screen recording

**Marker times come from the transcript.** Asking the model to timestamp its own
output means getting plausible invented numbers: it has no clock, only text. So
every summary item is matched against the lines by wording (the share of the
item's words found in a line; threshold 0.34) and takes that line's time. No
convincing match, no marker — an empty spot is honest, a marker pointing at the
wrong minute is not. Notes taken during a call already know their time and need
no matching at all.

Matching compares word stems — the first five letters — not whole words. The
document and the transcript almost never agree literally: the decision says
«обсуждение остановлено» while the line says «остановить». As whole words those
are different words, and the marker went missing exactly where people expected
it. Five letters is the length at which Russian forms of one word converge while
different words still diverge.

**Video rides the same stream as the audio.** ScreenCaptureKit is already open
for the system audio, so the picture is a second output (`.screen`) on the same
`SCStream`, and the filter decides what is captured: the whole display or the
windows of one application. No extra permission, and certainly no virtual
driver. Frames go to mp4 through `AVAssetWriter`, timed from the start of the
recording so that markers computed on the audio point at the same moment in the
video.

The capture is deliberately modest: 8 fps at 1600 px wide. This is a screen
share, not a film; an hour costs hundreds of megabytes instead of tens of
gigabytes, and 60 fps buys nothing in a recording of a call.

**The picture goes on and off mid-call** — a screen is shared for ten minutes of
an hour, and recording video of an empty desktop for the rest is pointless. The
helper is not restarted for it: that would cut the audio, and there is no second
take of a call. Instead it listens for commands on standard input
(`video-on <app> <file>`, `video-off`) and reconfigures the stream it already
has, via `updateConfiguration`/`updateContentFilter`: a real frame size while
recording, a 2×2 stub while not. The `.screen` output is always attached, even
when no picture is expected — there would be no way to add an output to a
running stream.

Reconfiguration is not instant, and the first frames after it still arrive at
the old size. Such a frame cannot go into a file of a different size, so the
frame's dimensions are checked against the writer's before it is appended.

**A segment switched on mid-call becomes a separate file.** A recording only
becomes the main video when it ran from the start of the call to its end
(`till_end`): only then are picture and sound counted from the same point and
the markers lead where they promise. Everything else is
`<recording>.screen-N.mp4` with its own slice of the audio (`_add_sound(...,
since=)` trims the track from the same second). Attaching a picture that is
missing for fifty minutes out of sixty to an hour-long recording means markers
that point at the wrong place, and the person would be the last to find out.

**Frame timestamps must increase strictly — and this is not a detail.**
ScreenCaptureKit delivers frames in bursts, and two of them easily land inside
the same 1/600 of a second. A repeated timestamp does not cost a frame, it costs
the recording: `AVAssetWriter` moves to `.failed`, silently drops everything
after it, and never writes the index. The file stays on disk, weighs megabytes
and opens nowhere — which is exactly how screen recording broke on its first
real use (1.6.1). So a colliding timestamp is nudged 1/600 forward, and the
return values of `startWriting` and `append` are checked and logged: a failure
that passes in silence is the worst kind.

**The index is written last.** `AVAssetWriter` keeps frames in `mdat` and writes
`moov` on close, so an interrupted recording looks like a real mp4 without being
one. The app checks for `moov` by walking the top-level atoms
(`media.playable_mp4`, no ffmpeg involved) and never shows a broken file — not
in the file list, not in the player. For the same reason shutdown finalises the
video **before** `stopCapture`: if that call stalls, the helper is killed on a
timeout, and by then the file is already whole.

**A capture log.** The helper talks about itself on stderr, and the app now
reads that stream throughout the recording into `.work/capture.log`. It used to
be read only on a crash, so a failure in the middle of a call left no trace at
all. It also removes a risk: an unread pipe eventually fills up and stalls the
helper itself.

**A local server for the player.** The window is loaded from a file, and WebKit
will not let a page play other files from disk: `<video src="file:///…">` stays
silently empty. So the media is served by a tiny http server on 127.0.0.1 with a
random port, restricted to the recording folders and to known extensions. The
important part is the `Range` header: without it a player cannot seek, and
seeking is the whole point.

## One icon set, shipped inside

The five icons in the window were drawn by hand, one at a time, and it showed:
different stroke weights, different corner radii, a window that looked
assembled from spare parts. They now all come from Iconsax (outline style, a
24×24 grid), taken from the Panda Icons Library community file in Figma.

The typographic stand-ins went with them: the `×` on every remove button and
the `›` on the documents accordion were letters pretending to be icons. They
are the set's own glyphs now — the cross is the plus rotated 45°, which is how
Iconsax draws it too.

The paths live in a single `ICONS` object in `app/ui/index.html` and the colour
is always `currentColor`, so both themes, hover and highlight states work
without a second copy of anything, and swapping the whole set costs one edit.
There are no icon files and no network: the app promises to work offline, and
that promise covers letters and icons as well.

The terms are CC BY 4.0 — with attribution — which is in Settings, in the
README and in `app/ui/icons/LICENSE-Iconsax.md`. That file also records a
contradiction: the Iconsax site says CC BY 4.0 while the `LICENSE` file in
their repository contains GPLv3. Fine while the app is given away as is; worth
settling before it is ever sold.

## Shelves: pinning, folders, renaming

Recordings sit on disk as a flat list of files, and that is the right call:
Finder shows them, they survive a reinstall, the archive rebuilds itself from
them. But past a hundred recordings a flat list stops helping.

**The folders are not folders on disk** (`app/shelf.py`). Moving the files into
real directories would rewrite paths already recorded in every `.result.json`
and in the links to attached documents. So a folder is a label, the tree is
drawn in the window, and the files stay where Finder expects them. The shelf is
keyed by the same identifier the archive uses (a hash of the path).

**Renaming moves the files too.** The app invents a title and does not always
get it right. Fixing it only inside the document would leave the old name in
Finder, and one recording would be called two things. So `library.retitle()`
moves every file of the recording and its attachments folder, then rewrites
`meta`. The path changes, so the identifier changes — `shelf.move()` carries the
pin and the folder across, or a shelf would be lost on every title fix.

## Picking what to record from the screen

A dropdown of application names did not work: one app can have several windows
open and the name does not say which. So the choice is a window of tiles, the
way Zoom and Telemost do it: the helper snapshots each source
(`SCScreenshotManager`, macOS 14+) and returns a small jpeg as a data URI.

Snapshots cost seconds, so the list is gathered two ways: without pictures on
every environment poll, with pictures (`list-apps --shots`) only when the person
opens the picker. The window opens immediately on what is already known and
fills in when the snapshots arrive. On macOS 13, which has no such API, the
tiles show an icon instead and the choice still works.

## Speaker marking: the human outranks the machine

A name put on a line during a call is not a caption, it is a fact: a person said
who is speaking. That fact used to survive only by luck. The transcript is
assembled from voice-cluster keys, and a name from a mark reached the file only
if it won its cluster outright — more than half the recognised time. If it did
not, it was discarded along with `line.speaker`, which is to say along with the
very marking it was made for.

The order is reversed now (`record._honour_tags`): the human is asked first. A
marked name takes the cluster where that person spoke most, if the cluster is
still automatically labelled; otherwise it gets a key of its own and the marked
lines move to it. An automatic caption (“Speaker 2”) is no obstacle to a human;
another person's confirmed name is.

A second bug lived in the same place: `_enrolled()` read `self.session` instead
of the session being processed. Since processing moved into a queue that yields
to a new recording, that meant taking the marks from a different call, or none.

**Correcting after the fact.** Splitting errs in small ways: two or three lines
drift to a neighbour. That could only be fixed a whole voice at a time. Now the
speaker name in the transcript is clickable and the line can be given to someone
else (`library.reassign`) — the recording's files are rewritten, speaking time
recounted, emptied voices removed. This is not about reading: **voice prints are
learned from exactly this marking**, and learning someone else's audio under your
name is worse than not learning at all.

Voices with the same label are folded into one (`_fold_same_names`). Otherwise
the person appeared twice in the card and their print was learned twice from
half the lines each.

## The schedule

**The question a schedule must answer is not “what is on today” but “will this
clash”.** Meetings arrive in different calendars — work Gmail, personal Yandex,
someone else's Outlook — and while agreeing on a time mid-call nobody remembers
them all. So the list is assembled from every source at once and overlaps are
marked (`agenda.mark_overlaps`).

**Events come from the system Calendar, not from the services' APIs**
(`capture/main.swift`, the `calendar-*` commands over EventKit). Gmail, Outlook
and Yandex already sync there — the first two natively, Yandex over CalDAV. One
system permission replaces three integrations, and nothing leaves the machine to
read a calendar.

The other way would have cost dearly: Google's calendar is a sensitive scope, so
serving anyone but the author needs domain verification, a privacy policy, a
demo video and a 3–5 day review, and without it a user cap and a “Google hasn't
verified this app” screen — plus an Azure registration for Outlook and a
separate CalDAV path for Yandex.

**Own events live alongside** (`.work/agenda.json`): not every agreement reaches
a calendar. One button sends such an event into the real calendar so that
everyone else sees it.

**What counts as a call.** Calendars are full of birthdays and “collect the
parcel”. The marker is a video link (Meet, Zoom, Teams, Telemost, Kontur Talk,
Jitsi and the rest) or two attendees and not an all-day event. Reminders default
to calls only.

**There are several reminder intervals, and any call can have its own**
(`agenda.parse_reminders`, `reminders_for`, `set_reminders`). A single “half an
hour ahead” does not cover the cases: someone else's standup needs five minutes,
a call with a client needs a day to prepare for. Settings pick the intervals as
buttons and take a custom number of minutes in the same place; any event in the
schedule can override the set, and an empty set puts it back on the general one.

Fired marks are stored per “event + interval” pair, so “an hour ahead” and “five
minutes ahead” do not interfere and each arrives exactly once. If the app was
closed and several marks came due at once, the person gets one reminder — the
one nearest to now — and the rest are retired silently: a queue of near-identical
messages on startup is worse than no message. A call that started more than five
minutes ago does not remind at all.

**A next call agreed in conversation** is found in the summary
(`agenda.suggest`) by the same date parsing that dates the tasks: “созвонимся
завтра в 15:30” becomes a concrete time, and the card offers to add it, saying
if that slot is already taken. It only offers: a meeting placed in a calendar on
a model's guess is worse than no meeting. No time named, no offer — “let's talk
next week” is an intention, not a meeting.

**Recording started from an event** takes its title and attendees. The names go
straight into “who's on the call” — and those are the names voice prints are
learned by, so the calendar pays for itself by feeding parts that already exist.

## The only place anything leaves the machine

The whole app promises to work without a network. `app/notify.py` breaks that
promise — deliberately, on explicit consent, and within very narrow limits.

Messengers are off by default. The settings say exactly what goes out: the time,
the title and, as separate ticks, the attendees and the summary. No transcripts,
no audio. Requests are outbound only; the single read is a one-off `getUpdates`
when the person presses *Find it* so the app can discover their chat — otherwise
the chat id has to be dug out through third-party bots, which is where this kind
of setup is usually abandoned.

**The macOS banner is honestly unreliable.** `display notification` via osascript
appears as Script Editor, and if its notifications are off the message vanishes
silently with no way to tell from inside. So the in-window banner always works,
the system one is an extra, and a *Test* button sits beside it so the person can
see with their own eyes whether it works for them. Telegram solves the same
problem better — a reminder half an hour ahead is more use on a phone than on
the Mac.

MAX is written from the documentation (`platform-api2.max.ru`, token in the
header) and has not been tested against a live bot: there is nowhere to get one
in the cloud environment. The service's own error is shown to the person as is —
it says what is wrong with the token or the chat.

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
