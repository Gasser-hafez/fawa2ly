"""
Question / request detector, v1.2 (heuristic).

Given the text of one STT piece (a sentence, possibly cut off), decide whether
the speaker asked something that expects an answer. classify() judges one
sentence; detect() judges a whole segment (any sentence).

Rules only, no ML, ~0 ms. Every rule change is scored with
`python -m fawa2ly.eval docs/eval/*.labels.jsonl` before it is merged.

v1.2 (2026-09-08), from the first 582 labeled pieces (interview6-8):
  - minimum length: Deepgram cuts questions into pieces, and 21 of 33 false
    alarms were fragments such as "What", "Why", "here?", "our company?".
    A "?" sentence needs 3+ words; an opener/wh/yes-no sentence needs 3+ words
    when complete and 6+ when cut off (no final punctuation).
  - yes/no questions: "Do you prefer...", "Are you interviewing..." (aux + subject).
  - prefixes: a vocative ("Chris, can you...", "That being said, what else...")
    and interjections ("please tell me", "And and what does this look").
  - wh-word needs a question-shaped continuation ("what would", "how many",
    "what else"); "what I kind of would describe", "what sets you apart" are
    relative clauses, not questions. "which" is dropped from the list.
"""

import re
from typing import Optional

# Openers that make a sentence a request even without a "?".
_REQUEST_OPENERS = (
    r"tell me",
    r"tell us",
    r"walk me through",
    r"walk us through",
    r"talk (me|us)? ?(through|about)",
    r"describe",
    r"explain",
    r"give (me|us) (an example|a sense|some examples)",
    r"share (with (me|us))? ?(an example|a time|how|what|your)",
    r"i'?d (like|love) to (hear|know|understand)",
    r"i'?m curious",
    r"(can|could|would) you",
    r"what about",
    r"how about",
)

# "Chris, ..." / "That being said, ..." (up to three words then a comma)
# A name or a short lead-in phrase before the opener, up to five words and a comma: "Chris, can
# you..." and also "For that chat app, can you walk me through...". v1.2 allowed three words,
# which was enough for a name and missed rehearsal4's lead-in; in turn mode that sentence reached
# the close with a period instead of a question mark, so the whole turn went unanswered.
_VOCATIVE = r"(?:[\w'-]+(?:\s+[\w'-]+){0,4},\s*)?"
# "so", "well,", "please", "and and ..." before the actual opener
_INTERJECTIONS = r"(?:(?:so|well|okay|ok|now|then|maybe|please|and|but|alright|great|um|uh)[,.]?\s+)*"
_PREFIX = r"^\W*" + _VOCATIVE + _INTERJECTIONS

_OPENER_RE = re.compile(_PREFIX + r"(" + "|".join(_REQUEST_OPENERS) + r")\b", re.IGNORECASE)

_AUX = (r"do|does|did|are|is|were|was|can|could|would|will|should|shall|have|has|had|"
        r"don't|doesn't|didn't|aren't|isn't|wasn't|weren't|won't|wouldn't|couldn't|shouldn't|haven't|hasn't")
_YESNO_RE = re.compile(_PREFIX + r"(" + _AUX + r")\s+(you|your|we|there|it|this|that|anyone|anybody|someone|anything|i)\b",
                       re.IGNORECASE)

# Wh-word at the start, followed by something that only a question continues with.
# "when"/"where"/"which" are excluded: they usually open subordinate or relative clauses.
_WH_RE = re.compile(_PREFIX + r"(what|why|how|who)\b\W*(" + _AUX +
                    r"|am|else|kind|kinds|sort|many|much|about|type|types|long|often|come|far|soon|exactly|specifically|other)\b",
                    re.IGNORECASE)

# Arabic (2026-09-11, v1.4). Deepgram's Arabic models (ar, ar-EG) end a question with "؟", and
# none of the English cues above can match an Arabic sentence, so in rehearsal8 an interviewer's
# Arabic questions ("إزيك يا كريم عامل إيّه ؟") reached the detector as statements. The Arabic
# question mark now counts like "?", and a sentence that opens with a request ("احكيلي عن...",
# "ممكن تشرح...") or a question word (Egyptian and Modern Standard) counts without one, under
# the same word minimums as English.
#
# v1.5 (2026-09-11): that still missed every Egyptian question in rehearsal8, because Deepgram's
# ar-EG model punctuated 4 of its 17 Arabic finals and none of the Egyptian ones. A whole
# interviewer turn reaches the detector as one sentence with no "؟" and no final punctuation, and
# three assumptions carried over from English punctuation each rejected it:
#   - the request sat in the middle of the turn ("حلو جدا يا كريم بما انك بدات تهتم بمجال الاتش ار
#     ممكن تحكي لي ايه اول تجربه"), and the patterns were anchored at the start;
#   - Egyptian puts the question word last ("عامل ايه", "شايف نفسك فين"), and v1.4 required a word
#     after it;
#   - no final punctuation read as "cut off" and needed 6 words ("احكي لي عن نفسك باختصار" has 5).
# So Arabic text is normalised first (diacritics, tatweel, alef and taa marbuta forms, which
# Deepgram writes inconsistently: "إيّه", "ايه"), requests and question words match anywhere, a
# yes/no verb ("عندك", "تقدر") matches at the start or after a discourse marker ("طيب يا كريم
# عندك اي اسئله"), "X ولا Y" is a choice question, and the minimum is 3 words with or without
# punctuation. A false alarm costs one model call, a miss costs the whole answer (see detect()).
# Patterns below are written in the normalised spelling.
_AR_LETTERS = re.compile(r"[؀-ۿ]")
_AR_MARKS = re.compile(r"[ً-ْـ]")
_AR_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_AR_START = r"(?<!\w)(?:و|ف)?"       # a word start, with "and" / "so" attached ("واحكيلي")
_AR_END = r"(?!\w)"
_AR_ME = r"(?:لي|لنا|ني|نا|ي)?"       # "me" / "us" written into the verb ("احكيلي", "تشرحلي", "تقولي")
_AR_REQUESTS = (
    r"احكي" + _AR_ME, r"قول لي", r"قول" + _AR_ME, r"كلم(?:ني|نا)",
    r"اشرح" + _AR_ME, r"وضح" + _AR_ME, r"اوصف" + _AR_ME, r"صف(?:لي|لنا)",
    r"حدث(?:ني|نا)", r"اخبر(?:ني|نا)", r"عرف(?:ني|نا)",
    r"ممكن ت(?:حكي|قول|شرح|كلم|وضح|دي|عرف|وصف|فهم)" + _AR_ME,
    r"(?:عايزك|عاوزك|محتاجك) ت\w+",
    r"(?:عايز|عاوز|عايزين|عاوزين|حابب|حابين) (?:اعرف|نعرف|اسمع|نسمع|افهم|نفهم)",
    r"(?:اديني|ادينا|تديني|تدينا) مثال",
    r"(?:خلينا|تعالي) نتكلم عن",
)
_AR_QUESTION_WORDS = (
    r"ازاي", r"ليه", r"ايه", r"مين", r"امتي", r"فين", r"كام", r"هل",
    r"لماذا", r"كيف", r"ماذا", r"متي", r"اين", r"كم", r"ما هو", r"ما هي", r"من هو", r"من هي",
)
_AR_MARKERS = r"(?:طيب|طب|تمام|ماشي|حلو|جميل|كويس|اوكي|اوك|بس|يعني|جدا|يا \w+)"
_AR_YESNO_VERBS = (r"عندك", r"تعرف", r"بتعرف", r"جربت", r"سبق", r"سبقلك", r"تقدر", r"ينفع",
                   r"تحب", r"بتحب", r"حابب", r"مستعد", r"موافق", r"فاكر", r"اشتغلت",
                   r"تستخدم", r"بتستخدم", r"استخدمت")
_AR_REQUEST_RE = re.compile(_AR_START + r"(" + "|".join(_AR_REQUESTS) + r")" + _AR_END)
_AR_WH_RE = re.compile(_AR_START + r"(" + "|".join(_AR_QUESTION_WORDS) + r")" + _AR_END)
_AR_YESNO_RE = re.compile(r"(?:^\W*|(?<!\w)" + _AR_MARKERS + r"[،,]?\s+)(?:انت |انتي )?(" +
                          "|".join(_AR_YESNO_VERBS) + r")" + _AR_END)
# "Provider ولا Riverpod", "تدريب عملي ولا نشاط جامعي"; not "ولا حاجه" (nothing) or "ولا حد" (nobody)
_AR_CHOICE_RE = re.compile(r"\w\s+ولا\s+(?!(?:حاجه|حد|مره|واحد|اي|ايه)(?!\w))\w")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟])\s+")
_TERMINAL = ".?!؟"
MIN_WORDS_QUESTION_MARK = 3
MIN_WORDS_COMPLETE = 3
MIN_WORDS_CUT_OFF = 6


def sentences(text: str) -> list[str]:
    """Split text on sentence-final punctuation. The last item may be an unfinished sentence."""
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def _classify_arabic(s: str) -> Optional[str]:
    n = _AR_MARKS.sub("", s).translate(_AR_FORMS)
    if _AR_REQUEST_RE.search(n):
        return "request_opener"
    if _AR_YESNO_RE.search(n) or _AR_CHOICE_RE.search(n):
        return "yes_no"
    if _AR_WH_RE.search(n):
        return "wh_word"
    return None


def classify(sentence: str) -> Optional[str]:
    """One sentence -> 'question_mark' | 'request_opener' | 'yes_no' | 'wh_word' | None."""
    s = sentence.strip()
    if not s:
        return None
    # A question mark on its own is not a word: Deepgram's Arabic output puts a space before "؟".
    n_words = len([w for w in s.split() if w not in ("?", "؟")])
    if s.endswith(("?", "؟")):
        return "question_mark" if n_words >= MIN_WORDS_QUESTION_MARK else None
    complete = s[-1] in _TERMINAL
    if _AR_LETTERS.search(s):
        # Egyptian Arabic from Deepgram carries no punctuation, so a missing full stop says
        # nothing about the sentence being cut off (v1.5).
        if n_words < MIN_WORDS_COMPLETE:
            return None
        why = _classify_arabic(s)
        if why is not None:
            return why
    elif n_words < (MIN_WORDS_COMPLETE if complete else MIN_WORDS_CUT_OFF):
        return None
    if _OPENER_RE.search(s):
        return "request_opener"
    if _YESNO_RE.search(s):
        return "yes_no"
    if _WH_RE.search(s):
        return "wh_word"
    return None


def detect(text: str) -> tuple[bool, str]:
    """
    Returns (is_question, reason). reason is one of:
    'question_mark' | 'request_opener' | 'yes_no' | 'wh_word' | 'none'
    Every sentence is judged; the segment is a question if ANY sentence is.
    Decision (M1, 2026-09-07): v1 judged only the last sentence, and missed
    "Could you tell me about yourself? Yeah." because the listener's "Yeah" got
    glued to the same segment. A missed question costs ~4 s (we wait for the next
    trigger); a false positive costs one cheap LLM call. So we bias toward firing.
    The reason reported is from the LAST matching sentence.
    """
    result = (False, "none")
    for sent in sentences(text):
        why = classify(sent)
        if why is not None:
            result = (True, why)
    return result
