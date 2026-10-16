"""Arabic calls, and a shorter direction profile (2026-09-11, maintainer's requests).

Arabic went wrong in three places, each checked here without a network:
  - the detector knew only "?" and English cues, so Deepgram's Arabic questions ("... ؟") and
    requests ("احكيلي عن ...") were statements (sentences from rehearsal2 and rehearsal8);
  - the guardrail's repeat check kept only a-z, so every Arabic line after the first was a
    "duplicate" of it;
  - nothing told the model to answer in Arabic.
"""

import pytest

from fawa2ly.context import ContextBuilder, filter_bullets
from fawa2ly.context.builder import ARABIC_LINES
from fawa2ly.limits import DIRECTION_WORDS
from fawa2ly.llm.prompts import system_for
from fawa2ly.turn.detector import classify, detect, sentences


@pytest.mark.parametrize("sentence, expected", [
    ("إزيك يا كريم عامل إيّه ؟", "question_mark"),                   # rehearsal8
    ("كيف حالك يا كريم ؟", "question_mark"),                          # rehearsal8
    ("إزاي تثير عملي ؟", "question_mark"),                            # rehearsal8
    ("احكي لي شويه عن نفسك وتجربتك مع فلار ومشروع واحد", "request_opener"),   # rehearsal2
    ("احكيلي عن آخر مشروع اشتغلت عليه", "request_opener"),
    ("ممكن تشرح إزاي عملت المزامنة في التطبيق", "request_opener"),
    ("طيب يا كريم، ممكن تحكي عن مشكلة واجهتك", "request_opener"),
    ("إزاي بتتعامل مع ضغط الشغل لما المواعيد تقرب", "wh_word"),
    ("ليه سبت شغلك اللي كنت فيه قبل كده", "wh_word"),
    ("ما هي أكبر نقطة ضعف عندك", "wh_word"),
])
def test_arabic_questions_and_requests_are_heard(sentence, expected):
    assert classify(sentence) == expected


# Whole interviewer turns as Deepgram's ar-EG model returns Egyptian speech: no punctuation, the
# request or question word anywhere, English terms written in Arabic letters.
@pytest.mark.parametrize("turn, expected", [
    ("طبعا جهز نعمل مقابله عمل تدريبيه لدور موارد بشريه اول سؤال احكي عن نفسك ومسارك باختصار",
     "request_opener"),                                                             # rehearsal8
    ("طيب خلينا نبدا احكي لي عن نفسك باختصار", "request_opener"),                   # rehearsal8
    ("احكي لي عن نفسك باختصار", "request_opener"),       # rehearsal8; 5 words, and nothing is cut off
    ("حلو جدا يا كريم بما انك بدات تهتم بمجال الاتش ار ممكن تحكي لي ايه اول تجربه خلتك تحس انك عايز",
     "request_opener"),                                                             # rehearsal8
    ("كان في تدريب عملي مثلا ولا نشاط جامعي", "yes_no"),                            # rehearsal8
    ("اهلا يا كريم إزيك عامل إيّه النهارده", "wh_word"),     # the question word last, with a shadda
    ("شايف نفسك فين بعد خمس سنين", "wh_word"),
    ("المرتب اللي انت متوقعه كام تقريبا", "wh_word"),
    ("طب لو الابلكيشن بتاعك بقى بطيء جدا هتعمل ديباجينج إزاي", "wh_word"),
    ("عندك أي اكسبيرينس مع اليونت تستينج", "yes_no"),
    ("طيب يا كريم عندك أي أسئلة ليا", "yes_no"),
    ("بما إنك اشتغلت على فلاتر ممكن تشرحلي الفرق بين الاتنين", "request_opener"),
    ("انت بتستخدم ستيت مانجمنت ايه بروفايدر ولا ريفربود", "yes_no"),
    ("أنت تستخدم state management A", "yes_no"),     # hr-egyptian-ar turn 14, as Deepgram cut it short
])
def test_unpunctuated_egyptian_turns_are_heard(turn, expected):
    assert classify(turn) == expected


@pytest.mark.parametrize("turn", [
    "نعمل مقابله عمل تدريبيه لدور موارد بشريه",                                      # rehearsal8
    "طيب خلينا نبدا",                                                                # rehearsal8
    "انا نور الاتش ار مانجر هنا الانترفيو هتاخد حوالي نص ساعه ان شاء الله",
    "تمام ده كويس جدا احنا شغالين في الفينتك من سنه الفين وعشرين والتيم عندنا حوالي عشرين ديفيلوبر",
    "ماشي شكرا جدا يا كريم هنبعتلك الفيدباك خلال اسبوع",
    "لو عندك ديدلاين قريب",                        # the first half of a question, before a pause
    "مفيش ولا حاجه اتغيرت في الخطه",
])
def test_unpunctuated_egyptian_statements_are_not(turn):
    assert classify(turn) is None


@pytest.mark.parametrize("sentence", [
    "ماشي اكيد",                                          # rehearsal2
    "تمام عالم بيك في المقابله يلا نبدا",                  # rehearsal2
    "فخور بيه بشكل فص",                                    # rehearsal2
    "كيف حالك ؟",                                          # two words: small talk, as in English
])
def test_arabic_statements_and_small_talk_are_not(sentence):
    assert classify(sentence) is None


def test_the_arabic_question_mark_ends_a_sentence():
    assert sentences("تمام يلا نبدا. إزاي بتتعامل مع الضغط ؟ ماشي") == [
        "تمام يلا نبدا.", "إزاي بتتعامل مع الضغط ؟", "ماشي"]
    assert detect("تمام يلا نبدا. إزاي بتتعامل مع الضغط ؟ ماشي") == (True, "question_mark")


def test_english_is_judged_as_before():
    assert classify("Can you tell me about yourself?") == "question_mark"
    assert classify("Tell me about a time you failed.") == "request_opener"
    assert classify("That sounds great.") is None
    assert classify("here?") is None


def test_every_arabic_line_of_an_answer_is_kept_and_a_real_repeat_is_not():
    lines = ["اشتغلت على تطبيق شات بفلاتر وفايربيز",
             "المزامنة كانت أصعب جزء لما النت يقطع",
             "اشتغلت على تطبيق شات بفلاتر وفايربيز"]
    kept, dropped = filter_bullets(lines)
    assert kept == lines[:2]
    assert [d.reason for d in dropped] == ["duplicate"]


def test_an_arabic_call_asks_for_arabic_lines_and_an_english_call_is_unchanged():
    assert ARABIC_LINES in ContextBuilder(lang="ar-EG").system()
    assert ARABIC_LINES in ContextBuilder(lang="ar", mode="meeting").system()
    assert ContextBuilder(lang="en").system() == ContextBuilder().system()
    assert ContextBuilder(lang="multi").system() == ContextBuilder().system()


def test_direction_lines_are_short_moves():
    for mode in ("interview", "meeting"):
        text = system_for("direction", mode)
        assert f"at most {DIRECTION_WORDS} words" in text
    move = "Open with the chat app's offline sync"
    too_long = "Open with the chat app and then explain the offline sync you built"
    assert len(too_long.split()) > DIRECTION_WORDS
    kept, dropped = filter_bullets([move, too_long], style="direction")
    assert kept == [move] and dropped[0].reason == "too_long"
    assert filter_bullets([too_long], style="full")[0] == [too_long], "the full profile keeps its 20 words"
