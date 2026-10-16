"""
Speaking pace and filler words, from the user's own
microphone.

Pace is words per minute over the user's last WINDOW_WORDS words, from the word times Deepgram
sends with every final result. Only talking counts as time: each word's own duration, and the gap
to the next word when it is shorter than PAUSE_S. A longer gap is the user listening or thinking,
and counting it would call a thoughtful speaker slow. A filler word takes time but is not a word.

Filler words are the ones Deepgram returns with filler_words=true: uh, um, mhmm, mm-mm, uh-uh,
uh-huh, nuh-uh. Without the option Deepgram removes uh and um from the transcript, and it is offered
for English only (developers.deepgram.com/docs/filler-words). Checked 2026-09-11 by streaming the
same spoken sentence both ways: with the option "um," and "uh," came back as words with their own
times, without it they were gone. So fillers are counted on English calls only, and a call in
another language gets its pace alone.
"""

from __future__ import annotations

from typing import Optional

FILLERS = {"uh", "um", "mhmm", "mm-mm", "uh-uh", "uh-huh", "nuh-uh"}
WINDOW_WORDS = 40      # the live number follows the last half minute or so of talking
MIN_WORDS = 12         # fewer words than this is not a pace yet
PAUSE_S = 0.8          # a gap this long or longer is not talking


def is_filler(text: str) -> bool:
    return text.lower().strip(".,!?;:\"'") in FILLERS


def _talk_seconds(words: list[tuple[float, float, bool]]) -> float:
    total = 0.0
    for i, (start, end, _) in enumerate(words):
        total += max(0.0, end - start)
        if i + 1 < len(words):
            gap = words[i + 1][0] - end
            if 0 < gap < PAUSE_S:
                total += gap
    return total


class PaceMeter:
    def __init__(self, count_fillers: bool = True):
        self.count_fillers = count_fillers
        self.recent: list[tuple[float, float, bool]] = []   # (start, end, is filler), oldest first
        self.words = 0                                      # the whole call, fillers not included
        self.fillers = 0
        self.seconds = 0.0                                  # the whole call's talking time

    def feed(self, words) -> bool:
        """Add one final result's words (stt.base.Word). True when anything was added."""
        added = False
        for w in words:
            if not str(w.text or "").strip():
                continue
            filler = is_filler(w.text)
            if self.recent:
                gap = w.start - self.recent[-1][1]
                if 0 < gap < PAUSE_S:
                    self.seconds += gap
            self.seconds += max(0.0, w.end - w.start)
            self.recent.append((w.start, w.end, filler))
            if filler:
                self.fillers += 1
            else:
                self.words += 1
            added = True
        del self.recent[: -WINDOW_WORDS * 3]    # room for the window's words and the fillers among them
        return added

    def snapshot(self) -> dict:
        window, counted = [], 0
        for item in reversed(self.recent):
            window.append(item)
            if not item[2]:
                counted += 1
                if counted >= WINDOW_WORDS:
                    break
        window.reverse()
        seconds = _talk_seconds(window)
        wpm: Optional[int] = round(counted / seconds * 60) if counted >= MIN_WORDS and seconds > 0 else None
        average: Optional[int] = (round(self.words / self.seconds * 60)
                                  if self.words >= MIN_WORDS and self.seconds > 0 else None)
        return {"wpm": wpm, "average_wpm": average, "words": self.words,
                "fillers": self.fillers if self.count_fillers else None, "english": self.count_fillers}
