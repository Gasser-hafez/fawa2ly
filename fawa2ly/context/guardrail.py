"""
Output guardrail.

The prompt asks for bullets within the limits in fawa2ly/limits.py. Models comply most of
the time; this is the part that does not depend on them complying. Anything dropped is returned with a reason
rather than discarded silently, so a prompt regression shows up as a rising drop count instead
of a quieter overlay.

A bullet is refused when it is:
  style          the shape the model chose for this answer ("STYLE: keywords"), on the Dynamic
                 profile, read by the suggester and never shown (roadmap item 27, 2026-09-13)
  too_long       over the word limit (the overlay cannot show it while someone is listening)
  label_prefix   a heading and a list of tags ("Acme Tax internship: Laravel, Git, code review")
                 instead of a sentence anyone would say out loud
  duplicate      the same line again, ignoring case, punctuation and bullet markers
  over_limit     correct but beyond the fifth kept line

2026-09-10: `full_sentence` removed. It dropped any finished sentence in the speaker's voice,
which was right while the product showed keywords and is exactly backwards now that prompt v2.0
asks for lines to say out loud. `label_prefix` replaces it and rejects the shape the maintainer
rejected on sight: a short label, a colon, then a comma-separated list of CV vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..limits import (DIRECTION_WORDS, KEYWORD_WORDS, MAX_BULLETS, MAX_BULLET_WORDS, STYLE_MARK)

MAX_WORDS = MAX_BULLET_WORDS        # the product rule; see fawa2ly/limits.py
# "dynamic" is what an answer is filtered by until the model says which shape it chose, and when it
# never says: the most permissive of the three, so an unmarked answer is shown rather than emptied.
# The trap it avoids is specific - a keyword answer that arrived without its marker would be judged
# by the full rules, where "Chat app: offline queue" is a label_prefix, and every line of it would be
# dropped for looking exactly like what a keyword cue is supposed to look like.
WORD_LIMIT = {"full": MAX_BULLET_WORDS, "keywords": KEYWORD_WORDS, "direction": DIRECTION_WORDS,
              "dynamic": MAX_BULLET_WORDS}

# A label of a few words, a colon, then the rest: "Personal action: clarified ownership, ...".
# Bounded to the start of the line, because a colon deeper in is ordinary punctuation
# ("what I would do differently: agree interfaces first" reads fine when said aloud).
_LABEL_PREFIX = re.compile(r"^\W*[A-Za-z][\w'/&-]*(?:[ ,-][\w'/&-]+){0,3}:\s+\S")
# Letters of any script, for spotting a repeated line. Until 2026-09-11 this kept only a-z and
# digits, so every Arabic line reduced to an empty key and each one after the first was dropped
# as a "duplicate" of it: an Arabic answer showed one line at most.
_NORMALISE = re.compile(r"[^\w ]+")
# The shape the model chose, on the Dynamic profile. Tolerates the bold
# and the quotes models put around a marker, and a trailing full stop, because this line arrives
# before any answer and a marker that is not recognised costs the whole shape.
_STYLE_LINE = re.compile(r"^[\W_]*" + re.escape(STYLE_MARK.rstrip(":")) + r"[\s*_]*:[\s*\"'`]*(\w+)[\s*\"'`.]*$",
                         re.IGNORECASE)


@dataclass
class Dropped:
    text: str
    reason: str


def _key(text: str) -> str:
    return _NORMALISE.sub("", text.lower()).strip()


def style_choice(line: str) -> Optional[str]:
    """The shape named in a "STYLE: keywords" line, or None when the line is not one or names
    something that is not a shape."""
    from ..llm.prompts import SHAPES
    m = _STYLE_LINE.match(line.strip())
    if m is None:
        return None
    chosen = m.group(1).lower()
    return chosen if chosen in SHAPES else None


def reason_to_drop(bullet: str, style: str = "full") -> str | None:
    text = bullet.strip()
    if not text:
        return "empty"
    if _STYLE_LINE.match(text):
        return "style"
    if len(text.split()) > WORD_LIMIT.get(style, MAX_WORDS):
        return "too_long"
    # A heading and tags is the wrong shape only for a line meant to be spoken. For a keyword cue
    # it is the point, and a direction line may name its move first ("Close on: ...").
    if style == "full" and _LABEL_PREFIX.match(text):
        return "label_prefix"
    return None


def filter_bullets(bullets: list[str], max_bullets: int = MAX_BULLETS,
                   style: str = "full") -> tuple[list[str], list[Dropped]]:
    """Return (kept, dropped). Order is preserved; the first `max_bullets` survivors are kept."""
    kept: list[str] = []
    dropped: list[Dropped] = []
    seen: set[str] = set()
    for raw in bullets:
        text = raw.strip()
        reason = reason_to_drop(text, style)
        if reason is None and _key(text) in seen:
            reason = "duplicate"
        if reason is None and len(kept) >= max_bullets:
            reason = "over_limit"
        if reason is not None:
            dropped.append(Dropped(text, reason))
            continue
        seen.add(_key(text))
        kept.append(text)
    return kept, dropped


def summarise(dropped: list[Dropped]) -> str:
    """'2 too_long, 1 duplicate' for a log line; empty string when nothing was dropped."""
    if not dropped:
        return ""
    counts: dict[str, int] = {}
    for d in dropped:
        counts[d.reason] = counts.get(d.reason, 0) + 1
    return ", ".join(f"{n} {reason}" for reason, n in sorted(counts.items()))
