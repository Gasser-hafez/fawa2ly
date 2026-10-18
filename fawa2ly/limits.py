"""
The product rules for what may appear on screen.

They live in their own module because two places must agree on them: the prompt that asks the
model for lines, and the guardrail that rejects the ones that came back too long. When the
numbers were written out separately, the prompt said 12 and the model answered with 14, and the
guardrail silently threw away half of every answer.

2026-09-08: raised from 12 to 15 words (maintainer's decision). Measured on interview7, 128 bullets
from 27 questions: at 12 words 69 were rejected and 4 questions showed nothing at all; at 15,
17 were rejected and 2 showed nothing. Bullet length in practice is p50 13, p90 17.

2026-09-10: raised to 20 (maintainer's decision, with the switch to spoken lines in prompt v2.0).
The limit no longer bounds a keyword string but a sentence someone has to say out loud, and
spoken sentences do not fit in 15: the four lines the maintainer accepted as the target shape measure
12, 12, 12 and 8 words, so 15 leaves no room for the ones that run longer, exactly as 12 did
before it. Re-measure the drop count on the next relevance run rather than moving it again by
feel.
"""

MAX_BULLETS = 5
MAX_BULLET_WORDS = 20

# Reply budget. Thinking models charge their hidden reasoning to it and report none of it, so a
# small ceiling shows up as a bullet cut mid-phrase rather than as an error (2026-09-08).
# 2026-09-10: it lived in three places at once, and they disagreed. `SuggestionRequest` defaulted
# to 2000, `Suggester` passed 800, and `ContextBuilder.build` passed 400, which is what the
# relevance eval actually ran on: the 14-of-30 truncations recorded at the time were measured
# at 400 while the note explaining them discussed 800. One name now, read by all of them.
DEFAULT_MAX_TOKENS = 2000

# The keywords profile (2026-09-11): cues to glance at, not sentences to say. Eight words holds a
# short label and a few terms ("Chat app: offline queue, Firebase sync") and nothing longer.
KEYWORD_WORDS = 8

# The direction profile (2026-09-11, maintainer: "shorter and easy to read"). Each line is one move to
# make, told in a verb and its object ("Open with the chat app's offline sync"); ten words holds
# that and stops the lines from turning back into sentences to say.
DIRECTION_WORDS = 10

# Documents one call may carry, all of them together (roadmap item 5, 2026-09-11). They are put in
# the prompt whole (ADR-7: stuffing), so this is what keeps that prefix bounded. A CV runs to about
# 500-1,500 words; 6,000 leaves room for a CV and a portfolio, or a CV and a meeting agenda, and is roughly 8,000 tokens of English. A document that would pass it is refused, not cut:
# a silently truncated price list is worse than a message. Revisit when time to first token is
# measured with a full budget.
MAX_DOCUMENT_WORDS = 6000

# The largest file a window may hand over to be read (a CV, an agenda). The socket's frame limit is
# derived from it (server.py), so the limit that gives the user a message is reached first; the
# windows check it too before sending (main.js and settings.js MAX_UPLOAD_BYTES: keep the three equal).
MAX_UPLOAD_BYTES = 10 * 2**20

# The Dynamic answer profile (roadmap item 27, 2026-09-13). The model picks the shape that fits each
# question - full, keywords or direction - instead of the user choosing one for the whole call, and
# says which on the FIRST line: STYLE_MARK and the name, nothing else. First, not last and that is forced by the streaming path: each line is passed through the guardrail
# the moment it arrives (assist.py), and the guardrail's word limit and its label rule differ per
# shape, so the shape has to be known before the first line of the answer is judged. The line is
# never shown.
STYLE_MARK = "STYLE:"

# A recap (roadmap item 17) of a call the user looked away from. Every other request carries the last
# few turns; a recap that carried only those would recap the part they just heard, which is the one
# part they do not need. It gets the call so far, bounded by words rather than turns because turns
# vary from a word to a paragraph: about twenty minutes of talk, which is longer than anyone looks
# away for, and still a fraction of the prompt budget.
RECAP_WORDS = 3000
