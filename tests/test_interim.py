"""Early detection on interim results and audio-clock de-duplication (M1, 2026-09-08)."""

from fawa2ly.pipeline import Pipeline, Sentence, sentences_from_words, split_words_by_speaker
from fawa2ly.stt.base import STTEvent, Word
from fawa2ly.turn.detector import classify


def words(spec, shift=0.0):
    """spec: list of (text, start, end, speaker). shift moves every timestamp (jitter)."""
    return [Word(t, s + shift, e + shift, spk) for t, s, e, spk in spec]


def interim(t_recv, t_rel, spec, shift=0.0):
    ws = words(spec, shift)
    return STTEvent("interim", t_recv, t_rel, " ".join(w.text for w in ws), ws)


def final(t_recv, t_rel, spec, shift=0.0):
    ws = words(spec, shift)
    return STTEvent("final", t_recv, t_rel, " ".join(w.text for w in ws), ws)


QUESTION = [("Why", 40.0, 40.2, 0), ("did", 40.2, 40.4, 0), ("you", 40.4, 40.5, 0),
            ("leave", 40.5, 40.8, 0), ("your", 40.8, 41.0, 0), ("last", 41.0, 41.3, 0), ("job?", 41.3, 41.8, 0)]
ANSWER = [("It", 42.0, 42.1, 0), ("was", 42.1, 42.3, 0), ("graveyard.", 42.3, 43.0, 0)]


def test_classify_single_sentences():
    assert classify("What hours can you work?") == "question_mark"
    assert classify("Tell me about yourself.") == "request_opener"
    assert classify("What would you say are some of your strengths") == "wh_word"
    assert classify("What would you say") is None          # cut-off fragment, too short to fire on
    assert classify("Do you prefer a specific type of work") == "yes_no"
    assert classify("I worked there.") is None
    assert classify("") is None


def test_sentences_from_words_marks_incomplete_tail():
    ws = words([("Oh,", 1.0, 1.2, 0), ("I", 1.2, 1.3, 0), ("see.", 1.3, 1.6, 0),
                ("What", 2.0, 2.2, 0), ("hours", 2.2, 2.5, 0)])
    assert sentences_from_words(ws) == [Sentence("Oh, I see.", 1.0, 1.6, True),
                                        Sentence("What hours", 2.0, 2.5, False)]
    assert sentences_from_words([]) == []


def test_split_words_by_speaker_runs():
    ws = words([("Hi?", 0.0, 0.5, 0), ("Yeah.", 0.9, 1.2, 1), ("Ok.", 1.3, 1.5, 1)])
    runs = split_words_by_speaker(ws)
    assert [(spk, [w.text for w in r]) for spk, r in runs] == [(0, ["Hi?"]), (1, ["Yeah.", "Ok."])]


def test_interim_question_fires_early_and_final_does_not_repeat_it():
    pipe = Pipeline()
    # 1. interim shows the full question plus the start of the answer
    out = pipe.feed(interim(100.0, 42.2, QUESTION + ANSWER[:2]))
    assert len(out) == 1
    u = out[0]
    assert (u.n, u.speaker, u.trigger, u.source, u.segments) == (1, 0, "question_mark", "interim", 1)
    assert u.text == "Why did you leave your last job?"
    assert u.question_end_to_fire_ms == 400          # 42.2 - 41.8
    assert u.speech_to_text_ms == 400
    # 2. next interim repeats the same words (cumulative) -> nothing new
    assert pipe.feed(interim(101.0, 43.2, QUESTION + ANSWER)) == []
    # 3. the final covers question + answer -> only the answer remains buffered, no second fire
    assert pipe.feed(final(102.0, 44.2, QUESTION + ANSWER)) == []
    out = pipe.feed(STTEvent("utterance_end", 103.0, 45.2))
    assert len(out) == 1
    assert (out[0].trigger, out[0].source, out[0].text) == ("silence", "final", "It was graveyard.")
    assert out[0].speech_to_text_ms == 2200          # 45.2 - 43.0
    assert out[0].question_end_to_fire_ms is None


def test_final_timestamp_jitter_does_not_cause_duplicate():
    pipe = Pipeline()
    assert len(pipe.feed(interim(100.0, 42.2, QUESTION))) == 1
    # the final's word timings are 120 ms later than the interim's and the wording changed
    shifted = [("Why", 40.0, 40.2, 0), ("did", 40.2, 40.4, 0), ("you", 40.4, 40.5, 0),
               ("leave", 40.5, 40.8, 0), ("your", 40.8, 41.0, 0), ("last", 41.0, 41.3, 0),
               ("job?", 41.3, 41.8, 0)] + ANSWER
    assert pipe.feed(final(102.0, 44.2, shifted, shift=0.12)) == []
    assert pipe.buf == ["It was graveyard."]


SELF = [("Tell", 0.0, 0.2, 0), ("me", 0.2, 0.3, 0), ("about", 0.3, 0.5, 0), ("yourself.", 0.5, 1.0, 0)]
FLUTTER = SELF[:3] + [("yourself", 0.5, 1.0, 0), ("and", 1.1, 1.2, 0), ("your", 1.2, 1.3, 0),
                      ("experience", 1.3, 1.7, 0), ("with", 1.7, 1.8, 0), ("Flutter.", 1.8, 2.3, 0)]


def test_incomplete_interim_sentence_does_not_fire():
    pipe = Pipeline()
    assert pipe.feed(interim(1.0, 1.0, [("Tell", 0.0, 0.2, 0), ("me", 0.2, 0.3, 0), ("about", 0.3, 0.5, 0)])) == []
    assert pipe.feed(interim(1.0, 1.0, [("What", 0.0, 0.2, 0), ("is", 0.2, 0.3, 0), ("your", 0.3, 0.5, 0)])) == []
    assert pipe.feed(interim(2.0, 2.0, SELF)) == []          # complete, but held to see if it grows
    out = pipe.feed(interim(3.0, 3.0, SELF))                 # nothing followed it: the speaker stopped
    assert len(out) == 1 and out[0].trigger == "request_opener" and out[0].source == "interim"


def test_a_question_that_keeps_growing_fires_once_with_the_whole_thing():
    """The first live rehearsal, 2026-09-10. "Tell me about yourself." is a whole sentence and
    half a question; the half that decides the answer was "and your experience with Flutter".
    Firing on the first half sent the model a question nobody asked."""
    pipe = Pipeline()
    assert pipe.feed(interim(1.0, 1.2, SELF)) == []
    # the speaker carried on, so the short sentence is dropped and the longer one takes its
    # place - and waits its own turn, because it too could still be growing
    assert pipe.feed(interim(2.0, 2.5, FLUTTER)) == []
    out = pipe.feed(interim(3.0, 3.5, FLUTTER))
    assert len(out) == 1
    assert out[0].text == "Tell me about yourself and your experience with Flutter."
    assert pipe.feed(final(4.0, 5.0, FLUTTER)) == [], "the question must not fire a second time"


def test_a_question_mark_still_fires_on_the_first_interim():
    """Holding costs about a second, so it is spent only where the sentence might not be the whole
    question. Nothing follows a question mark that changes what was asked."""
    pipe = Pipeline()
    out = pipe.feed(interim(1.0, 1.2, [("Why", 0.0, 0.2, 0), ("did", 0.2, 0.3, 0), ("you", 0.3, 0.4, 0),
                                       ("leave?", 0.4, 0.9, 0)]))
    assert len(out) == 1 and out[0].source == "interim" and out[0].trigger == "question_mark"


def test_a_held_question_is_superseded_by_the_final_and_fires_once():
    pipe = Pipeline()
    assert pipe.feed(interim(1.0, 1.2, SELF)) == []
    out = pipe.feed(final(2.0, 2.6, FLUTTER))
    assert len(out) == 1 and out[0].source == "final"
    assert out[0].text == "Tell me about yourself and your experience with Flutter."


def test_early_fire_includes_already_finalized_text_of_same_speaker():
    pipe = Pipeline()
    assert pipe.feed(final(10.0, 20.5, [("Okay.", 20.0, 20.3, 0)])) == []
    words = [("Well,", 20.5, 20.7, 0), ("tell", 20.7, 20.9, 0), ("me", 20.9, 21.0, 0),
             ("about", 21.0, 21.1, 0), ("yourself.", 21.1, 21.3, 0)]
    assert pipe.feed(interim(11.0, 21.5, words)) == []        # held for one event
    out = pipe.feed(interim(11.5, 22.0, words))
    assert len(out) == 1
    assert (out[0].text, out[0].segments, out[0].trigger) == ("Okay. Well, tell me about yourself.", 2, "request_opener")


def test_early_fire_after_other_speaker_flushes_that_speaker_first():
    pipe = Pipeline()
    assert pipe.feed(final(10.0, 5.2, [("I", 4.0, 4.1, 0), ("worked", 4.1, 4.5, 0), ("there.", 4.5, 5.0, 0)])) == []
    out = pipe.feed(interim(11.0, 6.5, [("Do", 5.5, 5.6, 1), ("you", 5.6, 5.7, 1), ("like", 5.7, 5.9, 1), ("it?", 5.9, 6.1, 1)]))
    assert [(u.speaker, u.trigger, u.source, u.text) for u in out] == [
        (0, "speaker_change", "final", "I worked there."),
        (1, "question_mark", "interim", "Do you like it?"),
    ]


def test_interim_detection_can_be_disabled_and_final_reports_question_end():
    pipe = Pipeline(interim_detection=False)
    assert pipe.feed(interim(100.0, 42.2, QUESTION)) == []
    out = pipe.feed(final(102.0, 45.0, QUESTION + ANSWER))
    assert len(out) == 1
    u = out[0]
    assert (u.trigger, u.source, u.text) == ("question_mark", "final", "Why did you leave your last job? It was graveyard.")
    assert u.speech_to_text_ms == 2000               # 45.0 - 43.0, end of the whole segment
    assert u.question_end_to_fire_ms == 3200         # 45.0 - 41.8, end of the question sentence


def test_interim_without_words_is_ignored():
    pipe = Pipeline()
    assert pipe.feed(STTEvent("interim", 1.0, 1.0, "Why did you leave?")) == []


def test_provisional_end_of_last_interim_word_does_not_leak_into_final():
    """interview6: 'job?' ended at 41.77 in the interim but 42.01 in the final."""
    pipe = Pipeline()
    q_interim = QUESTION[:-1] + [("job?", 41.61, 41.77, 0)]
    assert len(pipe.feed(interim(100.0, 42.1, q_interim))) == 1
    q_final = QUESTION[:-1] + [("job?", 41.69, 42.01, 0)]
    answer = [("It", 42.01, 42.2, 0), ("was", 42.2, 42.4, 0), ("graveyard.", 42.4, 43.0, 0)]
    assert pipe.feed(final(102.0, 44.5, q_final + answer)) == []
    assert pipe.buf == ["It was graveyard."]


def test_revised_question_in_later_interim_is_not_fired_twice():
    """interview6: 'questions for me?' became 'questions for me, Sam?' one interim later."""
    pipe = Pipeline()
    base = [("Do", 48.5, 48.6, 0), ("you", 48.6, 48.7, 0), ("have", 48.7, 48.9, 0), ("any", 48.9, 49.0, 0),
            ("questions", 49.0, 49.18, 0), ("for", 49.18, 49.26, 0)]
    out = pipe.feed(interim(100.0, 49.6, base + [("me?", 49.18, 49.26, 0)]))
    assert len(out) == 1 and out[0].text.endswith("for me?")
    assert pipe.feed(interim(101.0, 50.2, base + [("me,", 49.26, 49.42, 0), ("Sam?", 49.42, 49.9, 0)])) == []
    assert pipe.feed(final(102.0, 52.0, base + [("me,", 49.26, 49.42, 0), ("Sam?", 49.42, 49.9, 0),
                                                ("Yes.", 50.5, 50.8, 0)])) == []
    assert pipe.buf == ["Yes."]
    # a genuinely new question right after still fires
    out = pipe.feed(interim(103.0, 53.0, [("What", 51.0, 51.2, 0), ("kind", 51.2, 51.4, 0), ("of", 51.4, 51.5, 0),
                                          ("training", 51.5, 51.9, 0), ("is", 51.9, 52.0, 0), ("needed?", 52.0, 52.4, 0)]))
    assert len(out) == 1 and out[0].text == "Yes. What kind of training is needed?"


def test_a_question_cut_in_half_by_a_forced_final_fires_once_and_whole():
    """interview7: 'do you want this particular position here with on with' closed as one
    segment, 'our customer service department?' opened as the next.

    Until 2026-09-10 the first half fired on its own and the second was suppressed as a
    continuation, so the model was asked half a question and the rest was never seen. A final
    the provider forced (no endpointing) can cut a sentence anywhere, so its unfinished tail
    waits for the segment that completes it.
    """
    pipe = Pipeline()
    head = [("do", 111.2, 111.3, 1), ("you", 111.3, 111.4, 1), ("want", 111.4, 111.6, 1), ("this", 111.6, 111.8, 1),
            ("particular", 111.8, 112.3, 1), ("position", 112.3, 112.8, 1), ("here", 112.8, 113.0, 1),
            ("with", 113.0, 113.2, 1), ("on", 113.2, 113.4, 1), ("with", 113.4, 114.2, 1)]
    assert pipe.feed(final(100.0, 114.8, head)) == []
    tail = [("our", 114.2, 114.4, 1), ("customer", 114.4, 114.8, 1), ("service", 114.8, 115.2, 1), ("department?", 115.2, 116.0, 1)]
    out = pipe.feed(final(102.0, 116.6, tail))
    # the whole thing now ends in "?", so it is caught by the plainest rule there is
    assert len(out) == 1 and out[0].trigger == "question_mark"
    assert out[0].text == "do you want this particular position here with on with our customer service department?"
    assert pipe.feed(STTEvent("utterance_end", 103.0, 117.6)) == []


def test_an_endpointed_final_fires_even_without_final_punctuation():
    """When the provider says the speaker stopped, an unfinished tail is as complete as it gets;
    holding it would answer nothing at all."""
    pipe = Pipeline()
    words = [("so", 10.0, 10.2, 0), ("tell", 10.2, 10.4, 0), ("me", 10.4, 10.5, 0),
             ("about", 10.5, 10.8, 0), ("your", 10.8, 11.0, 0), ("last", 11.0, 11.3, 0), ("job", 11.3, 11.8, 0)]
    ev = final(100.0, 12.4, words)
    ev.endpointed = True
    out = pipe.feed(ev)
    assert len(out) == 1 and out[0].trigger == "request_opener"


def test_capitalized_question_shortly_after_a_fire_still_fires():
    pipe = Pipeline()
    assert len(pipe.feed(final(100.0, 11.5, QUESTION))) == 1
    nxt = [("Do", 12.0, 12.1, 0), ("you", 12.1, 12.2, 0), ("have", 12.2, 12.4, 0), ("any", 12.4, 12.5, 0), ("questions?", 12.5, 13.0, 0)]
    out = pipe.feed(final(101.0, 13.6, nxt))
    assert len(out) == 1 and out[0].text == "Do you have any questions?"


def test_lowercase_question_long_after_a_fire_fires():
    pipe = Pipeline()
    assert len(pipe.feed(final(100.0, 11.5, QUESTION))) == 1
    late = [("why", 50.0, 50.2, 0), ("the", 50.2, 50.3, 0), ("move?", 50.3, 50.8, 0)]
    out = pipe.feed(final(140.0, 51.4, late))
    assert len(out) == 1 and out[0].trigger == "question_mark"


def test_a_fired_question_is_revised_when_three_forced_finals_and_a_speaker_flip_finish_it():
    """rehearsal4, the case this exists for. The interim fired on "...how you handled state
    management?" as speaker 0. Deepgram then delivered the question in three finals, all as
    speaker 1: "For that chat app, can you walk me through how you" / "handled state management
    with provider and get x" / "and why you chose those tools." The last piece used to be
    suppressed as a tail and flushed as silence, so the model never heard "why"."""
    pipe = Pipeline()
    head = [("For", 49.8, 50.0), ("that", 50.0, 50.2), ("chat", 50.2, 50.4), ("app,", 50.4, 50.8),
            ("can", 50.9, 51.0), ("you", 51.0, 51.1), ("walk", 51.1, 51.3), ("me", 51.3, 51.4),
            ("through", 51.4, 51.7), ("how", 51.7, 51.9), ("you", 51.9, 52.0)]
    fired = pipe.feed(interim(10.0, 54.4, [(w, a, b, 0) for w, a, b in head] +
                              [("handled", 52.9, 53.3, 0), ("state", 53.3, 53.6, 0),
                               ("management?", 53.6, 54.2, 0)]))
    assert len(fired) == 1 and fired[0].revises is None

    assert pipe.feed(final(11.0, 54.9, [(w, a + 0.03, b, 1) for w, a, b in head])) == []
    assert pipe.feed(final(12.0, 58.1, [("handled", 52.94, 53.3, 1), ("state", 53.3, 53.6, 1),
                                        ("management", 53.6, 54.0, 1), ("with", 54.0, 54.5, 1),
                                        ("provider", 54.6, 55.0, 1), ("and", 55.0, 55.2, 1),
                                        ("get", 55.2, 55.5, 1), ("x", 55.5, 56.0, 1)])) == []
    last = final(13.0, 59.2, [("and", 56.2, 56.4, 1), ("why", 56.4, 56.6, 1), ("you", 56.6, 56.8, 1),
                              ("chose", 56.8, 57.2, 1), ("those", 57.2, 57.6, 1), ("tools.", 57.6, 58.3, 1)])
    last.endpointed = True
    out = pipe.feed(last)
    assert [u.revises for u in out] == [fired[0].n]
    assert out[0].text == ("For that chat app, can you walk me through how you handled state management "
                           "with provider and get x and why you chose those tools.")
    assert pipe.feed(STTEvent("utterance_end", 14.0, 60.1)) == [], "the second half must not come back as silence"


def test_the_answer_after_a_pause_is_not_merged_into_the_question():
    """interview7: the final dropped the "?" after "to the job", and the candidate's answer began
    0.88 s later. Growth that followed punctuation alone read "...detrimental to the job I think
    there's a risk when it comes to compassion." and re-asked that as the question."""
    pipe = Pipeline()
    q = [("Would", 10.0, 10.2), ("you", 10.2, 10.3), ("consider", 10.3, 10.7), ("any", 10.7, 10.9),
         ("weaknesses", 10.9, 11.5), ("that", 11.5, 11.6), ("you", 11.6, 11.7), ("have", 11.7, 11.9),
         ("to", 11.9, 12.0), ("be", 12.0, 12.1), ("detrimental", 12.26, 12.8), ("to", 12.8, 12.9),
         ("the", 12.9, 13.0)]
    fired = pipe.feed(interim(20.0, 13.8, [(w, a, b, 0) for w, a, b in q] + [("job?", 13.0, 13.4, 0)]))
    assert len(fired) == 1
    answer = [("I", 14.28, 14.4), ("think", 14.4, 14.6), ("there's", 14.6, 14.9), ("a", 14.9, 15.0),
              ("risk", 15.0, 15.4), ("when", 16.04, 16.2), ("it", 16.2, 16.3), ("comes", 16.3, 16.5),
              ("to", 16.5, 16.6), ("compassion.", 16.6, 17.3)]
    ev = final(21.0, 17.8, [(w, a, b, 0) for w, a, b in q] + [("job", 13.0, 13.4, 0)] +
               [(w, a, b, 0) for w, a, b in answer])
    ev.endpointed = True
    assert all(u.revises is None for u in pipe.feed(ev))

