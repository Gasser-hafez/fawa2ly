"""
Prompt v3.0 (M2). Lines the speaker can say out loud, asked for here and checked by the guardrail.
The context builder prepends the role preset and the background; this is the fixed part.

Framing note (2026-09-08): v1 opened with "a silent coach whispering to a job candidate during
a live interview". Models refused it outright ("the system reminder asks me to act as a cheating
aid"), and it contradicted the project's policy that stealth is a non-goal. v1.1 asks for practice
prompts that jog the speaker's own experience, which is what the product is.

Length note (2026-09-08): v1.1 said "at most 12 words" once, in a sentence full of other rules,
and the model averaged 13 with a long tail to 42, so the guardrail rejected 69 of 128 bullets.
v1.2 states the limit as its own rule with the count spelled out, shows the shape wanted, and
takes the number from fawa2ly.limits so the prompt and the guardrail cannot drift apart.

Clarification note (2026-09-08): replaying a recorded interview (a retail job) against a sample
CV (a software engineer) made the model stop and ask which person it was helping.
Reasonable of it, and useless: mid-call there is nobody to answer, and the overlay filled with a
40-word question instead of keywords. v1.3 forbids asking anything back and says to drop the
background silently when it does not fit.

Grounding note (2026-09-10): the first rated relevance run (a real CV) scored the
interview presets 4.70 / 4.20, and both weak answers failed in ways no rule covered. "A conflict
at work" produced a conflict the CV does not contain, told as if it happened. "Are you
interviewing elsewhere" produced a menu of mutually exclusive statuses for the speaker to choose
between mid-sentence. v1.4 added one rule against each.

Voice note (2026-09-10, maintainer's decision): **v2.0 asks for speech, not keywords.** Reading the
same run end to end, the maintainer's verdict was that none of it was usable in front of an
interviewer: every bullet was CV vocabulary glued together ("Acme Tax internship: small Laravel
team, shared repository, branch-based workflow"), a label and a list of tags rather than
anything a person says out loud. The keywords-only rule was written to keep the overlay
glanceable, and it did, but glanceable text nobody can speak is not an assist. Each line is now
a complete spoken sentence in the speaker's own voice, first person allowed, and the lines are
ordered so that read in sequence they form one answer. The word limit moved 15 -> 20 in the same
decision, because natural speech does not fit in 15 (see fawa2ly/limits.py), and the guardrail
swapped its `full_sentence` rule (which rejected exactly this) for `label_prefix` (which
rejects exactly the old shape).

Answer note (2026-09-10, from the first live rehearsal): v2.0's lines were true and sayable and
still not answers. Asked "tell me about yourself and your experience with Flutter", it opened
with where the speaker studies. Nothing was invented - every claim was in the CV - but the model
was reciting the background in a generic order instead of building an answer to the question in
front of it. The cause was one-sided grounding: v2.0 said where facts may come from and never
said that the choosing, connecting and framing are the model's own work, so the safe move was to
quote. v2.1 says both, and adds the rule that makes it checkable: the first line answers the
question head on, in the terms the question used.

Detail note (2026-09-10, maintainer's decision): an interviewer digs. Asked how the offline queue in
his chat app was built, the CV has the project but not the detail, and v2.1 answered by saying
how to approach the question, which is useless mid-sentence. v2.2 splits the grounding in two.
Identity is fixed by the background: employer, job, school, dates, which projects exist. Detail
inside a project the background names is the model's to fill in, concretely and plausibly,
consistent with the tools listed for that project, and never as scale, revenue or team size the
background does not support. (This note was meant to land with v2.2; the edit that carried it
matched nothing and failed silently, which is why it is written here after the fact.)

Relevance note (2026-09-10, rehearsal4): with grounding loosened, the model overcorrected and
put the CV into everything. "How do you keep your skills sharp", "how do you approach learning
from bugs" and "why Flutter" each came back built around the same chat app, and its offline
message queue appeared in 5 of 11 answers. Nobody talks like that: a general question gets a
general answer, with at most one example when it helps, and a story told once is not told again.
v2.3 tells the model which questions want a specific and which do not, and to look at the recent
conversation before reusing an example.

Profiles note (2026-09-11, maintainer's request): three answer profiles, chosen before a call and
changeable during one. `full` is v2.3 in shape: sentences to say. `keywords` gives cues of at most
eight words and allows a label, which the full guardrail rejects. `direction` gives two or three
lines on how to shape the answer - what to open with, which kind of example, what to land on -
rather than its words. The rules about truth and conduct (the grounding, never asking back, not
retelling a story) are one shared block of text, so the three profiles cannot drift apart on
the things that must not differ between them.

Interview note (same day, maintainer's request): every profile now picks what makes the interviewer
want to hear more - a concrete result, a decision and the reason for it, a problem they owned,
what they learned - and lets that substance do the impressing, instead of claims of passion,
hard work or being the best, which interviewers discount on hearing them.

Mode note: the call's mode is a first choice, interview or meeting, and each has its own prompt.
"""

from typing import Optional

from ..limits import DIRECTION_WORDS, KEYWORD_WORDS, MAX_BULLET_WORDS, MAX_BULLETS, STYLE_MARK

# The three shapes an answer can take, then "dynamic", which is not a fourth shape but a choice
# between the three, made per question by the model (roadmap item 27).
SHAPES = ("full", "keywords", "direction")
DYNAMIC = "dynamic"
STYLES = SHAPES + (DYNAMIC,)
MODES = ("interview", "meeting")

_OPENING = ("You help someone practise answering questions out loud. They are in an interview, "
            "real or mock, and have just been asked a question. ")

_INTRO = {
    "full": "Give them lines they can say, in their own voice, built from their own background.",
    "keywords": ("Give them keyword cues to glance at while they answer in their own words, drawn "
                 "from their own background."),
    "direction": ("Tell them how to shape their answer - the approach and the kind of content - "
                  "rather than the words, drawing on their own background."),
}

_SHAPE = {
    "full": [
        f"Give 3 to {MAX_BULLETS} lines, each starting with '- '.",
        "Every line is a complete, natural spoken sentence they could say word for word, exactly as a person talks. Read it back before you write it: if it sounds like a CV entry, a heading or a list of tags, rewrite it as speech.",
        "Never a label followed by a list. 'Acme Tax internship: small Laravel team, shared repository, branch workflow' is a database row and nobody says it out loud. Say 'In my internship, two of us kept editing the same Laravel files' instead.",
        f"At most {MAX_BULLET_WORDS} words per line. Count them. Longer than that cannot be read while someone is listening.",
        "The lines are an answer to the question that was actually asked, not a summary of the background. The first line answers it head on, in the terms the question used: asked about Flutter experience, the first line is about Flutter experience, not about where they study. The rest run on from it, and for a story question that means what happened, what they did, how it turned out, what they took from it.",
    ],
    "keywords": [
        f"Give 3 to {MAX_BULLETS} bullets, each starting with '- '.",
        "Each bullet is a cue, not a sentence: a few words the speaker glances at and then says in their own words. A short label and a few terms is right here: 'Chat app: offline queue, Firebase sync'.",
        f"At most {KEYWORD_WORDS} words per bullet. Count them.",
        "The bullets cover the answer to the question that was actually asked, in the order it should be said, and the first bullet is its core, in the terms the question used.",
    ],
    "direction": [
        "Give 2 or 3 lines, each starting with '- '.",
        f"Each line is one move, at most {DIRECTION_WORDS} words, starting with a verb: what to open with, which example to use, what to land on. It is not the words to say.",
        "Name the thing itself, never a category: 'Open with the chat app's offline sync', not 'Mention a relevant project'.",
        "The first line answers the question head on: what the answer is about, in the terms the question used.",
    ],
}

_SHARED = [
    "Match the kind of question. One about a project, a tool they used, or a time something happened needs a specific from their background. One about how they think, work, learn or what they want - why this technology, how they keep their skills sharp, how they deal with bugs - is answered in general terms, the way a person would answer it over coffee. Add a single short example from the background only if it makes that answer clearly stronger, and never more than one. Most general questions need none.",
    "Do not retell a story. If the recent conversation already used a project or an incident, reach for a different one or answer without one. An interviewer who hears the same example three times stops believing it.",
    "One point per line. Never offer alternatives to choose between ('actively interviewing, exploring selectively, or focused here'): pick the likeliest one and give only that. The speaker has no time to read a menu and decide.",
    "In an interview, choose what makes the interviewer want to hear more: a concrete result, a decision and the reason for it, a problem they owned, what they learned. Let that substance impress. Never have them call themselves passionate, hardworking or the best; interviewers discount those words the moment they hear them.",
    "The background fixes who they are and what they have worked on: never invent an employer, a job, a school, a date, or a project that is not in it, and never contradict it.",
    "Inside something the background does name, fill in the detail it leaves out. Asked how a feature in one of their projects was built, or what went wrong in it, give a concrete, ordinary answer that a developer who built that project would give, consistent with the tools the background lists for it. A plausible specific they can talk around beats \"I would need to check\": they are practising an answer, not filing a report. Keep invented detail modest and technical, never a claim of scale, revenue or team size the background does not support.",
    "If the background has nothing to do with what was asked, say how to approach the question instead of inventing a whole experience.",
    "Never ask a question back or request clarification. Nobody can reply to you: the person is mid-conversation and only sees your lines.",
    "If the background does not match the conversation, ignore the background silently and answer for the question that was asked.",
    "No introduction, no closing line, no headers.",
]


# Meeting mode (roadmap item 18, 2026-09-12): the user takes part in a work meeting, neither being
# evaluated nor selling. Unrated.
_MEETING_OPENING = ("You help someone taking part in a work meeting with colleagues, a client or a manager. "
                    "Someone in the meeting has just asked them something or turned to them. ")

_MEETING_INTRO = {
    "full": "Give them lines they can say in the meeting, in their own voice, built from the meeting material they have and what was said.",
    "keywords": ("Give them keyword cues to glance at while they reply in their own words, drawn from the "
                 "meeting material and what was said."),
    "direction": ("Tell them how to shape their reply - the approach and the kind of content - rather than the "
                  "words, drawing on the meeting material and what was said."),
}

_MEETING_SHAPE = {
    "full": [
        f"Give 3 to {MAX_BULLETS} lines, each starting with '- '.",
        "Every line is a complete, natural spoken sentence they could say in the meeting word for word. If it reads like a status report, a slide or a heading, rewrite it as speech.",
        f"At most {MAX_BULLET_WORDS} words per line. Count them. Longer than that cannot be read while the others are waiting.",
        "The first line answers what was actually asked, head on, in its terms: asked for a status, the first line is the status. The next give the reason or the detail behind it, and the last, when it helps, is the next step.",
    ],
    "keywords": [
        f"Give 3 to {MAX_BULLETS} bullets, each starting with '- '.",
        "Each bullet is a cue, not a sentence: a few words they glance at and then say in their own words. A short label and a few terms is right here: 'Release: Thursday, blocked on QA'.",
        f"At most {KEYWORD_WORDS} words per bullet. Count them.",
        "The bullets cover the reply in the order it should be said: the core of it first, then the detail behind it, then the next step.",
    ],
    "direction": [
        "Give 2 or 3 lines, each starting with '- '.",
        f"Each line is one move, at most {DIRECTION_WORDS} words, starting with a verb: what to report first, which detail to bring, what to propose next. It is not the words to say.",
        "Name the thing itself, never a category: 'Say the login bug is fixed', not 'Give an update'.",
        "The first line names what they were really asked for.",
    ],
}

_MEETING_SHARED = [
    "One point per line. Never offer alternatives to choose between: pick the likeliest one and give only that. The speaker has no time to read a menu and decide.",
    "Project facts, figures, dates, owners and decisions come only from the meeting material or what was said in the conversation. Never invent a status, a figure, a deadline or who did something. If neither covers it, have them say they will check and come back with it.",
    "Never commit them to a task, a deadline or a promise they have not already made in the conversation; a line may offer to take something on, said as an offer.",
    "Be direct and collaborative: agree with what is fair, disagree with a reason, never blame a colleague.",
    "A line may be a question for the others when it moves the meeting forward. Never ask a question of your own or request clarification: the speaker is mid-meeting and only sees your lines.",
    "If the meeting material does not match the conversation, ignore it silently and answer for what was asked.",
    "No introduction, no closing line, no headers.",
]

_PROMPTS = {"interview": (_OPENING, _INTRO, _SHAPE, _SHARED),
            "meeting": (_MEETING_OPENING, _MEETING_INTRO, _MEETING_SHAPE, _MEETING_SHARED)}


# ---- Dynamic (roadmap item 27, 2026-09-13) ----
# The user picks one shape for a whole call today, before they know what will be asked. Dynamic lets
# the model pick per question. Two things make that cheap enough to do mid-call:
#
# - **It costs no extra request and no extra wait.** The choice is made inside the answer itself, on
#   its first line, so there is no classifier call in front of the one the user is waiting on. The
#   product metric is time from the end of the question to the first line (section 0); a second round
#   trip would spend the whole budget deciding what to say rather than saying it.
# - **It does not break the prompt cache.** The prompt below is the same string for every question of
#   the call, so the cached prefix still matches. Switching `set_style` per question would rewrite
#   the prefix each time and pay for the whole background again - the cost the profile switch already
#   warns about (ContextBuilder.set_style).
#
# The three shape blocks are the same lists the fixed profiles use, so a rule changed in one cannot
# drift from the other. What is new here is only the choosing rule, and it is deliberately biased: full is
# the default and the other two have to earn the choice. A user who wanted cues can pick keywords for
# the whole call; a user on Dynamic who is handed cues for a question they cannot improvise around
# has been left worse off than the default would have left them.
_CHOOSE_HEAD = (f"Choose the shape that fits this question, then answer in it. The FIRST line of your "
                f"reply says which, exactly: '{STYLE_MARK} full', '{STYLE_MARK} keywords' or "
                f"'{STYLE_MARK} direction'. Nothing else on that line, and it starts with "
                f"'{STYLE_MARK}' whatever language the conversation is in. It is never shown to "
                f"anyone. Then give the lines for the shape you chose, and follow that shape's rules "
                f"below exactly - the number of lines and the word limit are its own.")

_CHOOSE = {
    "interview": [
        "full is the default. Choose it unless one of the others clearly fits better, and choose it whenever you are unsure. It is the only shape that gives them something to say when the question catches them out.",
        "keywords only when the answer is theirs to tell and they plainly have it: a question about a project, a job or a skill their background covers well, where they need the points and the order, not the words. Never for a question their background says little about.",
        "direction when how they answer matters more than the words: a question about approach or opinion, an awkward one (why they left, what they earn, a weakness, a gap), or one their background has nothing on, where words would have to be invented. Say how to handle it instead.",
    ],
    "meeting": [
        "full is the default. Choose it unless one of the others clearly fits better, and choose it whenever you are unsure.",
        "keywords only when they are being asked for something they know - a status, an update on their own work - and need the points and their order rather than the words.",
        "direction when how they reply matters more than the words: a disagreement, a decision they are being pushed on, a commitment they should not make on the spot, or something the meeting material does not cover.",
    ],
}


_DYNAMIC_INTRO = {
    "interview": ("Each question is different, so you choose how to help with this one: the lines to "
                  "say, the cues to glance at, or how to shape the answer - all built from their own "
                  "background."),
    "meeting": ("Each question is different, so you choose how to help with this one: the lines to "
                "say, the cues to glance at, or how to shape the reply - all built from the meeting "
                "material and what was said."),
}


def _dynamic_system(mode: str) -> str:
    """The Dynamic profile's prompt: one string for the whole call, holding all three shapes and the
    rule for choosing between them."""
    opening, _intro, shape, shared = _PROMPTS[mode]
    parts = [opening + _DYNAMIC_INTRO[mode], "", _CHOOSE_HEAD, "",
             "When to choose which:", *(f"- {r}" for r in _CHOOSE[mode]), ""]
    for name in SHAPES:
        parts += [f"Rules for {name}, when you choose it:", *(f"- {r}" for r in shape[name]), ""]
    parts += ["Rules for every shape:", *(f"- {r}" for r in shared)]
    return "\n".join(parts)


def system_for(style: str = "full", mode: str = "interview") -> str:
    """The fixed part of the prompt for one answer profile in one call mode: its shape rules, then
    the shared ones. Dynamic carries all three shapes and the rule for choosing between them."""
    if style not in STYLES:
        raise ValueError(f"unknown answer style {style!r}; one of {', '.join(STYLES)}")
    if mode not in MODES:
        raise ValueError(f"unknown call mode {mode!r}; one of {', '.join(MODES)}")
    if style == DYNAMIC:
        return _dynamic_system(mode)
    opening, intro, shape, shared = _PROMPTS[mode]
    return opening + intro[style] + "\n\nRules:\n" + "\n".join(f"- {r}" for r in shape[style] + shared)


SYSTEM_V1 = system_for("full")


def more_request(question: str, shown: list[str]) -> str:
    """The question again, with what is already on screen, for the overlay's More button."""
    listed = "\n".join(f"- {line}" for line in shown)
    return (f"{question}\n\nAlready on screen:\n{listed}\n\n"
            "Give 2 or 3 more lines that go deeper into this same answer: the detail, the reason or "
            "the result those lines leave out. Do not repeat or rephrase them.")


# On-demand actions (roadmap item 17, 2026-09-12): buttons that do not wait for a detected question.
# Each is (the card's title, what the model is asked); the conversation so far comes with it.
ACTION_LEAD = "They pressed a button for help right now; nobody asked a question:"
ACTIONS = {
    "say": ("What should I say now?",
            "Give lines they can say next, picking up from where the conversation is now."),
    "recap": ("Recap so far",
              "Recap the conversation so far, one point per line, in the order it came up. These lines are "
              "for them to read and catch up, not to say."),
    "ask": ("Questions to ask",
            "Give questions they could put to the other side next, one per line, that move the conversation "
            "forward. This time every line is a question for the other side, which is what they asked for."),
}


def shorter_request(question: str, shown: list[str]) -> str:
    """The question again, with the lines on screen, for the overlay's Shorter button."""
    listed = "\n".join(f"- {line}" for line in shown)
    return (f"{question}\n\nOn screen now:\n{listed}\n\n"
            "Say the same answer in 2 or 3 lines, each about half as long, keeping the point of each.")


# Negotiation help (roadmap item 25, 2026-09-13). Turned on for a call where money is going to come
# up, and added to the prompt only then, so every other call sends the prompt it sent before.
#
# It is a switch rather than a role, though the roadmap calls it one, because a role in this app
# replaces who is speaking: choosing "Salary negotiation" would cost the user the role that says they
# are a mobile developer, and the model would stop knowing what they do in the middle of the one
# conversation where what they do is the whole argument. A negotiation is a phase of a call, not a
# different speaker, so it composes with the role and the mode instead of replacing them.
#
# The grounding rule is the roadmap's and is the reason this is safe to ship: market figures come
# only from the user's documents or from numbers said in the conversation, never from the model. A
# model inventing "the usual range is X" during a live salary talk would be inventing the one number
# the user is about to repeat out loud. With no figure available the line is the move, not a number.
# The rule against claiming an offer that does not exist is here for the same reason from the other
# side: the obvious advice in a negotiation is to imply a competing offer, and that is advice to lie.
_NEGOTIATE_SHARED = [
    "Never invent a market figure, a going rate, a band or what other people or companies pay or charge. Use only a number that is in the documents or that someone said in this conversation, and say where it came from when it is used. If there is no such number, the line is the move to make, never a figure.",
    "Never have them claim something that is not true: an offer they do not have, a price another supplier gave them, a deadline that is not real, or a competitor they have not spoken to. It is the first thing that comes to mind in a negotiation and it is the one thing that ends badly.",
    "One move per line, and never a menu to choose between.",
    "Do not close for them. They decide what to accept or refuse; the lines get them the information and the room to decide.",
]

NEGOTIATION_RULE = {
    "interview": " ".join(["This call is a pay conversation. Help them negotiate:",
        "Let the other side put a number down first when that is still possible; asked for their expectations before they have one, a line that asks what has been budgeted for the role is better than a figure.",
        "When a figure has to be given, give a range built on what is in the documents or what has been said here, and say what it is based on.",
        "Counter on what they bring - what they have delivered, what the role needs, what they would own - never on what they need or what they are worth in the abstract.",
        "When the number cannot move, move something else and name it: start date, title, a review at six months, leave, a learning budget, notice period, working pattern.",
        *_NEGOTIATE_SHARED]),
    "meeting": " ".join(["This part of the meeting is a negotiation - a deadline, a budget, scope or who does the work. Help them:",
        "Do not let them agree to a date, a number or a piece of work on the spot when they do not yet know what it costs: a line that asks what would have to move is better than a yes.",
        "Trade rather than refuse: what could be dropped, moved or resourced instead, named specifically.",
        *_NEGOTIATE_SHARED]),
}


ASKED_LEAD = "They were just asked:"


def user_content(question: str, history: list[tuple[str, str]], context: Optional[str] = None,
                 lead: Optional[str] = None) -> str:
    parts = []
    if history:
        parts.append("Recent conversation:\n" + "\n".join(f"{spk}: {text}" for spk, text in history))
    if context:
        parts.append(context)
    parts.append(f"{lead or ASKED_LEAD}\n{question}")
    return "\n\n".join(parts)
