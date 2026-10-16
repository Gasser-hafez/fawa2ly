import pytest
from fawa2ly.turn.detector import detect

@pytest.mark.parametrize("text,expected,reason", [
    # from the M0 recording
    ("So why exactly are you interested in interviewing with our consulting firm?", True, "question_mark"),
    ("Okay. Awesome. That's good to know. So could you tell me about a time you solved a difficult problem?", True, "question_mark"),
    ("Maybe tell me about a time you were able to convince someone to change their mind.", True, "request_opener"),
    ("So next, could you tell me about like one or two of your key strengths?", True, "question_mark"),
    # imperative requests without "?"
    ("Walk me through your resume.", True, "request_opener"),
    ("Describe a situation where you had to lead a team.", True, "request_opener"),
    ("I'd love to hear about your last project.", True, "request_opener"),
    # wh-word with dropped "?"
    ("What would you say is your biggest weakness", True, "wh_word"),
    # not questions
    ("Yeah. My reasons for joining your consulting firm are twofold.", False, "none"),
    ("So that was able to save us a total cost 10% from the total project.", False, "none"),
    # question followed by filler from either speaker -> still fires (M1 decision, see detector docstring)
    ("Could you tell me a little bit about yourself? Yeah.", True, "question_mark"),
    ("What's your biggest weakness? Take your time.", True, "question_mark"),
    ("Why did that happen? Anyway, I think it was fine.", True, "question_mark"),
    ("when I was working at the electrical power plant. We were trying to", False, "none"),  # clause, not question
    ("", False, "none"),
])
def test_detect(text, expected, reason):
    got, why = detect(text)
    assert got == expected
    assert why == reason


@pytest.mark.parametrize("text,expected,reason", [
    # interjection + request opener (2026-09-08, missed on interview6)
    ("Well, tell me about yourself.", True, "request_opener"),
    ("Okay, describe your last role.", True, "request_opener"),
    ("So, walk me through the project.", True, "request_opener"),
    ("Well, I think that went fine.", False, "none"),
])
def test_detect_interjection_openers(text, expected, reason):
    assert detect(text) == (expected, reason)


@pytest.mark.parametrize("text,expected,reason", [
    # v1.2 (2026-09-08): scored on docs/eval, see detector docstring
    ("please tell me a little bit about yourself.", True, "request_opener"),
    ("Chris, can you tell me about a challenge", True, "request_opener"),
    ("That being said, what else do you know", True, "wh_word"),
    ("And and what does this look", True, "wh_word"),
    ("Do you prefer a specific type of work invite", True, "yes_no"),
    ("do you want this particular position here with on with", True, "yes_no"),
    ("Are you interviewing currently with any other companies?", True, "question_mark"),
    # fragments never fire, even with a "?" or a wh-word
    ("What", False, "none"),
    ("Why", False, "none"),
    ("here?", False, "none"),
    ("our company?", False, "none"),
    ("Can you", False, "none"),
    ("Would you consider", False, "none"),
    ("How did you hear", False, "none"),
    ("Can you give me an", False, "none"),
    ("Right?", False, "none"),
    # relative clauses that start with a wh-word
    ("what I kind of would describe as like the chronology of your", False, "none"),
    ("what sets you apart, what makes you truly different, and oftentimes, look at your", False, "none"),
    ("Which is based on my experience and education.", False, "none"),
    # complete short requests still fire
    ("Describe your role.", True, "request_opener"),
    ("Tell me about yourself.", True, "request_opener"),
])
def test_detect_v12(text, expected, reason):
    assert detect(text) == (expected, reason)


def test_a_short_lead_in_before_the_question_is_allowed():
    """rehearsal4, turn mode: the final ended "...why you chose those tools." with a period, and
    the sentence did not classify because the prefix allowed at most three words before the comma."""
    from fawa2ly.turn.detector import classify
    assert classify("For that chat app, can you walk me through how you handled state management "
                    "and why you chose those tools.") is not None
    assert classify("In my last job, I handled the release process.") is None

