# Detector eval set

One `<session>.labels.jsonl` per recorded session, produced by
`python -m fawa2ly.label <session>.raw.jsonl`. Scored with
`python -m fawa2ly.eval docs/eval/*.labels.jsonl`.

**This repository ships no labeled recordings.** The sets the detector was tuned on were transcripts
of other people's videos, which are not ours to publish. Record your own calls (with everyone's
consent), label them, and keep the result next to your changes. Calls can also be built from a script
with `python -m fawa2ly.synth` and `python -m fawa2ly.voice_fixture` (see `docs/eval/scripts/`).

Each row records `labeled_by`: `human` for interactive labeling, or a model name
for imported labels (`--questions ... --labeled-by <name>`). Model labels are
reviewed by a human through the eval's miss / false-alarm lists, which is far
cheaper than labeling every sentence.

## Labeling rule

The unit is a *piece*: one sentence of one final Deepgram segment, split by
speaker. Deepgram often cuts one spoken question into two or three pieces
("How did you hear" / "about the position open with our company?"), so judge
each piece on its own:

**`y` when a fire on this piece alone would be useful**, i.e. the piece carries
enough of a question or request that an assistant could start answering it:

- direct questions: "What hours can you work?"
- requests: "Tell me about yourself.", "Walk me through your resume."
- indirect questions: "I'd like to hear about your last project."
- the content-bearing half of a split question, even without its end:
  "describe a time you disagreed with a decision that was"

**`n` for everything else**, in particular:

- answers, small talk, statements, feedback
- fragments that carry no question content on their own, even when they end in
  `?`: "Why", "Can you", "here?", "our company?", "environment?"
- questions the speaker does not expect the other party to answer now:
  rhetorical ("Right?"), thinking aloud during an answer ("Are we talking weeks
  or months?"), quoted or hypothetical questions inside feedback
  ("They'll say, can you give me a sense of an accomplishment?")
- a name call on its own ("Sam?") unless it clearly means "are you Sam?"

Both halves of a split question can be `y` when each carries content; the
duplicate-fire problem that creates is a pipeline matter measured by replay,
not by this set.

## Why sentences, not utterances

Until 2026-09-10 the live detector ran per sentence on interim and final
results, so the eval had to be at the same granularity. Precision and recall
here are the M1 exit criterion (>= 85 % each) the project aimed for.

## Per turn

Since 2026-09-10 the app answers whole turns of the other side, closed by a
second of silence or a change of speaker, so
`python -m fawa2ly.eval docs/eval/*.labels.jsonl --turns` also scores the set
the way the app runs: each raw log (in `--raw-dir`, the project folder by
default) is replayed through the turn pipeline, every labeled piece is placed in
the turn that holds it by audio time, and a turn is a question when any of its
pieces is labeled `y`. The labels are the same; only the unit that is scored
changes. A turn that holds several questions counts once, so this score cannot
see questions merged into one card.
