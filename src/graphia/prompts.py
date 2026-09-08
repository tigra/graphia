"""Prompt constants for Moderator narration and public game lines."""

from __future__ import annotations

MODERATOR_SYSTEM = """You are the Moderator of Graphia, a social-deduction game.
Your voice is calm, neutral, and theatrical — a narrator presiding over a small
gathered circle of players. You never take sides and never reveal private
information unless the game rules explicitly require it. You announce phase
transitions, describe events, and prompt players for their decisions. Keep
lines short. Use present tense. Do not speak for the players. When a new
development lands, describe only what every player can observe in public.
"""

ROSTER_INTRO_TEMPLATE = "Players in this game: {names}. Let the game begin."

NAME_GEN_SYSTEM = """You are a name generator for a Mafia-style social-deduction
party game. You produce short, memorable, culturally varied first names that
feel natural around a game table. You never explain; you only return names.
"""

NAME_GEN_USER_TEMPLATE = """Generate exactly {count} distinct first names for AI players.
Requirements:
- One word each, no titles, no surnames, no numbers, no punctuation.
- Culturally varied — draw from different regions and traditions.
- All {count} must be distinct (case-insensitive) and non-empty.
Return them via the Roster schema as a `names` list of {count} strings.
"""

PERSONA_SYSTEM = """You are a character designer for Graphia, a Mafia-style
social-deduction party game. You invent a vivid, distinct persona for one AI
player: a personality, a characteristic manner of speaking, and a short
backstory. Personas are felt through how a character talks — they never change
the rules. Return only the structured fields; do not explain.
"""

PERSONA_CITIZEN_USER_TEMPLATE = """Design a persona for the player named {name}.

This is an honest, ordinary townsperson with nothing to hide — what they
present is who they truly are.

Provide:
- `personality`: a short, distinctive temperament (e.g. bold and brash; warm
  and cautious; dry and analytical).
- `manner`: how they speak — pacing, vocabulary, verbal tics.
- `public_backstory`: a brief, honest backstory (who they are, what they do).
- Leave `secret_backstory` empty.

Make this character distinct and memorable — anchor it on the name {name} so it
does not blur into other players.
"""

PERSONA_MAFIA_USER_TEMPLATE = """Design a TWO-LAYER persona for the player named {name}, who is secretly a Mafioso.

This player lives a double life and needs both a convincing cover and a true
self that only they know:

- `personality`: the temperament of the PUBLIC COVER they perform at the table.
- `manner`: how that public cover speaks.
- `public_backstory`: the LEGEND — a believable cover identity of an ordinary,
  trustworthy townsperson. This cover MUST NOT hint, in any way, that the
  character is a Mafioso, a criminal, or hiding anything. It should read as a
  perfectly innocent member of the town.
- `secret_backstory`: the TRUE SELF — a backstory consistent with being a
  Mafioso (who they really are, how they came to the Mafia), known only to this
  player.

Make this character distinct and memorable — anchor it on the name {name}. The
legend and the true self should both feel like the same person playing a part.
"""

# Spec 031 (Distinct AI Personas Across the Roster), option (b). When a game's
# AI characters are created one after another, each new character is shown the
# characters ALREADY created for that same game so it can be made deliberately
# different from them — instead of every persona being invented from a blank
# slate (which collapses into one modal townsperson). Appended as a SEPARATE
# ``HumanMessage`` by ``generate_personas`` rather than added as a ``{...}`` slot
# to the two persona user templates above — that keeps every existing
# ``.format(name=...)`` call site untouched (no spec-019-style KeyError blast
# radius). The ``{others}`` slot is filled in ``nodes/setup.py`` with the prior
# personas' TABLE-FACING text only (personality + manner + public_persona, one
# per line); a Mafioso's hidden ``true_self`` is NEVER threaded here. The first
# AI character of a game gets no such message (nothing yet to differ from).
PERSONA_DISTINCT_FROM_TEMPLATE = """You are creating one of several characters for the same game. Make this one clearly different in temperament and voice from the characters already created:
{others}
Avoid reusing their personality, manner, or backstory.
"""

# Spec 034 (Diversified Persona Generation). The persona model is strongly
# mode-seeking — left to itself it keeps reaching for the same calm, observant
# townsperson. This pool is a BROAD, deliberately-wider-than-the-model's-default
# range of temperaments; ``generate_personas`` draws one per AI player WITHOUT
# replacement within a game (via the module-global ``random``) and steers that
# character toward it, so the cast starts spread across the range instead of
# converging on one mode. The draw is appended as a SEPARATE ``HumanMessage``
# built from ``PERSONA_ARCHETYPE_HINT_TEMPLATE`` (NOT a ``{...}`` slot on the
# persona user templates — the same anti-``KeyError`` discipline as the spec-031
# distinct-from block), so every existing ``.format(name=...)`` call site is
# untouched. The hint is a STEER, not a straitjacket — a temperament to lean
# toward, layered over the role-tailored cover/honest guidance. It carries no
# allegiance signal (it colours manner/temperament only), so it is safe for both
# a Citizen's honest self and a Mafioso's public cover.
PERSONA_ARCHETYPES: tuple[str, ...] = (
    "vigilant and watchful",
    "brash and loud",
    "dry and sardonic",
    "warm and effusive",
    "anxious and second-guessing",
    "eccentric and unpredictable",
    "gruff and blunt",
    "earnest and sincere",
    "aloof and detached",
    "playful and teasing",
    "stern and commanding",
    "dreamy and distractible",
    "shrewd and calculating",
    "cheerful and irrepressible",
    "weary and world-worn",
    "prickly and easily-offended",
)

PERSONA_ARCHETYPE_HINT_TEMPLATE = (
    "Lean toward a {archetype} temperament — distinct from the other characters. "
    "Let that colour their personality and manner; do not make them the usual "
    "calm, observant townsperson."
)

MAFIA_TEAMMATE_INTRO_TEMPLATE = (
    "Your Mafia teammates are: {names}. You work together to eliminate "
    "Law-abiding Citizens. During the Night, you point at one target; "
    "there is no chat."
)

MAFIA_POINT_SYSTEM = """You are a Mafioso in Graphia, a social-deduction game.
During the Night, you and your Mafia teammates silently point at one Law-abiding
Citizen to eliminate. Think strategically: consider who is most dangerous to the
Mafia, who might be a hidden threat, and who the other players trust.

You must return ONLY the `target_id` field via the structured schema. Do not
speak. Do not narrate. Do not explain. Pick one `target_id` from the list of
alive Law-abiding Citizens provided to you.
"""

MAFIA_POINT_USER_TEMPLATE = """Alive Law-abiding Citizens (name: id):
{roster}
{mafia_persona}
Your Mafia teammates' picks so far this Night:
{prior_picks}
{private_thoughts}
The Mafia kill by AGREEMENT — the Night ends the moment every teammate points
at the same target. Move toward a shared target: if your teammates have already
converged on someone, you may change your pick to match them.

Pick exactly one `target_id` from the ids above. Return only the `target_id`
field.
"""

DAY_SPEAK_SYSTEM = """You are a player in Graphia, a Mafia-style social-deduction
party game. It is the Day phase: players speak in turn around the circle. Stay
in character as an observant player trying to advance your own side's victory.
Say something new on your turn — don't repeat or echo a point another player
has already made. Keep it short: one or two sentences at most.

Return `kind='speak'` with a one-sentence spoken line in `text`, OR
`kind='vote'` with the target's exact `target_id` to call a vote-to-execute.
When you have a genuine, specific suspicion, convert it into a vote
(`kind='vote'`) — that is how Law-abiding Citizens convict the Mafia and how
the Mafia misdirect suspicion onto the innocent. When you have no real lead,
speak (`kind='speak'`) to gather information. Do not call a vote every turn,
and do not accuse anyone without a reason. If you vote, leave `text` unset; if
you speak, leave `target_id` unset.
"""

# Role-specific closing guidance (spec 024). A strong, side-matched menu of the
# concrete plays available to the actor RIGHT NOW, injected at the TAIL of both
# Day prompts (recency position) so it is the most salient thing the model reads
# before acting. The two side-menus live here as the single source of guidance
# text; both call sites (``_ai_day_action`` / ``_ai_ballot``) consume them
# through the one ``_role_guidance_block`` builder in ``nodes/day.py``, so the
# speak-prompt and vote-prompt guidance can never drift. The shared
# ``ROLE_GUIDANCE_LABEL`` framing header is the structural marker the tests
# assert on and the ``GRAPHIA_ROLE_GUIDANCE`` ablation flag (ADR 011) toggles.
ROLE_GUIDANCE_LABEL = "Your side's plays right now:"

# Law-abiding menu: the town wins ONLY by executing Mafiosos, so the moves are
# active (spot → accuse → vote a genuine suspect) with the explicit caution not
# to get fellow Law-abiding Citizens executed and not to accuse baselessly.
ROLE_GUIDANCE_LAW_ABIDING = """The town wins ONLY by executing Mafiosos — drifting or commenting on the dead never wins. So:
- Watch for a likely Mafioso: weigh who has deflected, dodged, or pushed a vote onto someone innocent.
- Voice that suspicion openly — name the player you suspect and say why, rather than passive commentary.
- When you hold a genuine suspect, put them up for a vote-to-execute before the Day ends (`kind='vote'`).
- Take care NOT to get fellow Law-abiding Citizens executed: do not accuse without a reason. With no real lead, speak to gather information instead of pushing a baseless accusation."""

# Mafioso menu: win by deception. Maintain the cover, deflect onto Citizens,
# shield/coordinate with teammates, steer votes — all UNDER the standing
# never-reveal rule (reinforces ``_persona_block``; never instructs disclosure).
ROLE_GUIDANCE_MAFIA = """You win by deception, not by being caught. So:
- Hold your public cover persona — stay the ordinary, trustworthy townsperson the table sees.
- Cast suspicion onto Law-abiding Citizens; give the table a plausible innocent to doubt.
- Protect your fellow Mafiosos: avoid exposing a teammate, and quietly coordinate to keep suspicion off the Mafia.
- Steer votes toward Citizens and away from the Mafia (`kind='vote'` on a Citizen when it helps your side).
- NEVER reveal that you are Mafia, name your teammates, or admit your persona is a front — keep the cover at all times."""

DAY_SPEAK_USER_TEMPLATE = """You are {speaker} — your secret role is {role_label}. {win_condition}
{team_line}
{persona}
{standings}Alive players at the table (name: id):
{roster}

Recent public discussion:
{context}

Never publicly reveal your secret role or your teammates.
{private_thoughts}{role_guidance}
Take your turn now. Either reply with one or two sentences in character
(`kind='speak'`), or call for a vote against a specific `target_id` from the
roster above (`kind='vote'`).
"""

DAY_OPEN_VICTIM_REVEAL_TEMPLATE = (
    "Day breaks. {name} was killed last night. {name} was a {role_label}."
)

DAY_OPEN_NO_VICTIM_TEMPLATE = "Day breaks."

VOTE_INITIATE_ANNOUNCE_TEMPLATE = (
    "{initiator} has called for a vote to execute {target}."
)

VOTE_PER_BALLOT_TEMPLATE = "{voter}: {vote_label}"

VOTE_TALLY_TEMPLATE = "The tally: {yes_count} Yes, {no_count} No."

VOTE_EXECUTED_TEMPLATE = "{name} has been executed. {name} was a {role_label}."

VOTE_FAILED_TEMPLATE = "The vote fails."

# End-of-round Moderator status recap (spec 018). Present-tense, neutral
# Moderator voice. The decision-relevant standings BODY (side counts with
# singular/plural, the votes-called and executed-today clauses) is assembled in
# ``_render_standings`` (spec 019) and passed in as the finished ``{standings}``
# string; ``render_day_round_recap`` adds the "Day N, <clock> status:" framing
# here. The ``{clock}`` slot is spec 020's in-world game-time for the round (9 AM
# at round 1 advancing to midnight at round 6), recap-only — it sits beside the
# day number and is NOT part of the ``{standings}`` body fed to the AI prompts.
# That same ``_render_standings`` string is injected front-and-center into the
# AI Day-speak / vote prompts WITHOUT the clock, so the public recap and the AI
# prompts can never drift. The ``" status:"` substring is the test recap-detection
# marker and must stay intact; the ``{standings}`` body stays byte-identical.
#
# In the AI prompts the ``{standings}`` slot carries the WHOLE labelled block
# (``"Current standings (act on these):\n<body>\n\n"``), assembled by
# ``_standings_prompt_block`` in ``nodes/day.py``. That block is gated by the
# spec-019 recap-aware-reasoning ablation flag (ADR 011): with the flag OFF the
# slot collapses to ``""`` and the prompt reverts to its pre-019 form (no
# standings label and no body). This recap TEMPLATE is unaffected — it always
# wraps the bare ``_render_standings`` body.
DAY_ROUND_RECAP_TEMPLATE = "Day {day}, {clock} status: {standings}"

AI_VOTE_SYSTEM = """You are a player in Graphia, a Mafia-style social-deduction
game. The table has called a vote to execute a specific player. Your job is to
cast a single Yes/No ballot. Vote Yes to execute the target; vote No to spare
them. You know your own secret role and win condition (below); vote in the
interest of YOUR side's victory. Consider the public discussion and the
target's behaviour. Return only the boolean `yes` field via the structured
schema.
"""

AI_VOTE_USER_TEMPLATE = """You are {voter} — your secret role is {role_label}. {win_condition}
{team_line}
{standings}A vote has been called to execute {target}.
{relationship}
Recent public discussion:
{context}
{private_thoughts}{role_guidance}
Cast your ballot: `yes=True` to execute, `yes=False` to spare. Return only the
`yes` field.
"""

ENDGAME_WINNER_LAW = "The Law-abiding Citizens have won."
ENDGAME_WINNER_MAFIA = "The Mafia have won."
# Whole-game Day-cap hit (spec 023). A Mafia game has no natural draw — players
# always thin out to a winner — so reaching the Day cap signals a stuck/looping
# game, recorded distinctly as a runaway/unresolved game rather than a real
# result. Day-denominated text, matching the day-cap safeguard.
ENDGAME_WINNER_RUNAWAY = (
    "The game did not resolve and was stopped at the Day cap "
    "(runaway / unresolved game)."
)
# Retained (now Day-denominated) for the defensive ``winner == "draw"`` path in
# ``end_screen``; no live path produces ``"draw"`` since the cap became runaway.
ENDGAME_WINNER_DRAW = "The game ended in a draw at the Day cap."
ENDGAME_HEADER_KILLS = "Events this game:"
ENDGAME_HEADER_ROSTER = "Full roster:"
ENDGAME_PERSONA_HEADER = "Who they really were:"
# Honest persona for a Law-abiding AI: name, role, and the single self it showed.
ENDGAME_PERSONA_CITIZEN_TEMPLATE = (
    "• {name} ({role_label}) — {personality} {manner} {public_persona}"
)
# Mafioso reveal: contrast the cover legend it performed against its true self.
ENDGAME_PERSONA_MAFIA_TEMPLATE = (
    "• {name} ({role_label}) — publicly presented as {public_persona} "
    "({personality} {manner}) … but was really a Mafioso: {true_self}"
)

# Per-AI Day-round private thoughts (spec 028). At the close of each Day round,
# every surviving AI player privately reflects via this prompt — a short note,
# seen by no one else. Deliberately MILD: it invites the player to take stock and
# plan its OWN next move in its own voice, and does NOT prescribe a strategy — an
# explicit contrast to the directive ``ROLE_GUIDANCE_*`` menus of spec 024 (the
# two coexist: this gives the player a private place to reason; role-guidance is
# a closing nudge). The note is framed as thinking for itself, seen by no one.
REFLECTION_SYSTEM = """You are a player in Graphia, a Mafia-style social-deduction
party game. The Day's speaking round has just closed. Before the next round, take
a quiet moment to think privately — only for yourself. No one else will ever see
this: not the other players, not anyone. This is your own private train of
thought.

Take stock of how the conversation and the game are going from where you sit, and
think about what you might do next. Speak in your own voice, as yourself. This is
reflection, not a speech — there is no audience. Do not decide for anyone but
yourself, and do not feel you must commit to any particular move; just think.

Return a SHORT private note (one or two sentences) in the `thought` field.
"""

# The reflection user template. Mirrors the slot vocabulary of
# ``DAY_SPEAK_USER_TEMPLATE`` so the same node-side helpers populate it
# (``_role_label`` / ``_win_condition_line`` / ``_team_line`` / ``_persona_block``
# / ``_render_standings`` / ``_render_context``) plus ``{private_thoughts}`` —
# this player's OWN prior notes — so the reflection itself is grounded in the
# running train of thought. The mildness lives in the wording (the system
# prompt), not the slots.
REFLECTION_USER_TEMPLATE = """You are {speaker} — your secret role is {role_label}. {win_condition}
{team_line}
{persona}
{standings}A Day speaking round has just ended.

Recent public discussion:
{context}
{private_thoughts}
Write a short private note to yourself — one or two sentences, in your own voice —
taking stock and thinking about your own next move. Return only the `thought`
field.
"""

# ---------------------------------------------------------------------------
# Per-AI private diaries (spec 039)
# ---------------------------------------------------------------------------
# ONE entry per surviving AI player per day cycle, written at the Day→Night
# hinge. Distinct from spec 028's ``REFLECTION_*`` above along the two axes the
# functional spec names explicitly: GRAIN (a whole day's summing-up, not a
# reaction to one speaking round) and LENGTH (``DIARY_SENTENCE_BOUND`` below,
# deliberately larger than 028's "one or two sentences"). The two coexist and
# feed the same player; neither is ever shown to another player or to the human.
#
# THE DESIGN PROBLEM THIS WORDING SOLVES — do not "tidy" it into a checklist.
# The functional spec requires that different characters' entries differ in what
# they dwell on rather than all following one shape. That is precisely the
# MODE-SEEKING failure spec 034 diagnosed for persona generation: handed a list
# of things to cover, the model produces the SAME shape regardless of who is
# writing, and every persona collapses onto one voice. So ``DIARY_SYSTEM`` makes
# four moves, and each one is load-bearing:
#
#   1. NAME BOTH DIRECTIONS AND GRANT EITHER. Looking back over the day and
#      looking ahead to the night are BOTH named, and either — or both — is
#      explicitly permitted. Naming only one direction steers every character
#      the same way; naming both and granting the choice is what makes the
#      variation legitimate rather than disobedience.
#   2. ANCHOR ON VOICE, NOT CONTENT. The invitation leans on the ``{persona}``
#      block ALREADY present in the user template rather than adding new
#      character instruction — "a diary sounds like the person keeping it".
#      Content latitude plus a voice anchor is what produces per-character
#      difference; content instruction plus a voice anchor does not.
#   3. FORBID A FIXED FORM. No headings, no bullet points, no numbering, and no
#      summing-up-then-planning — that last is the specific mould the model
#      falls into unprompted, and it flattens the entries hardest.
#   4. KEEP 028's ANTI-STEER CLAUSE (carried over in substance, verbatim but for
#      "think"→"write"). The prompt must not push toward a particular move,
#      suspicion or target — the functional spec's SECOND acceptance criterion —
#      so a "be strategic" phrasing would break it. Directive play-menus are
#      spec 024's ``ROLE_GUIDANCE_*`` job on the DECISION prompts; the diary is
#      a private place to think, not a plan to file.
#
# DELIBERATELY ABSENT: any enumeration of topics to cover ("where things stand,
# who you suspect, what you mean to do"). An enumeration IS the checklist, and
# the checklist is the collapse — it would fail the third acceptance criterion
# while looking, in isolation, like a helpfully specific prompt. If a future
# reader is tempted to add one, that is the temptation this comment exists for.
#
# The bound is written down ONCE, as this constant interpolated into
# ``DIARY_SYSTEM``, so the prose and the number cannot drift. Note the STATED
# bound is only a request: the functional spec requires the length to hold for
# every entry actually written, which is the node's hard clamp
# (``DIARY_MAX_CHARS`` in ``nodes/day.py``), not this wording. Spec 028's
# ``REFLECTION_SYSTEM`` bytes are left untouched on purpose — 039 is additive,
# and "the diary's bound is the larger of the two" is checked by reading this
# constant against 028's unchanged prose.
DIARY_SENTENCE_BOUND = 6

DIARY_SYSTEM = f"""You are a player in Graphia, a Mafia-style social-deduction
party game. The Day is over and Night is about to fall. Before it does, you open
your diary and write today's entry. No one else will ever see it: not the other
players, not anyone. It is yours alone.

Write it as yourself, in your own voice — a diary sounds like the person keeping
it, not like a report. This is the whole day's entry, not a passing note about
one exchange in it.

Some people use a diary to look back over the day that has just ended; some use
it to look ahead to the night that is coming; some do both. Do whichever is more
like you — none of them is asked of you.

There is no set form. No headings, no bullet points, no numbering, no fixed
order, and no summing-up-followed-by-a-plan unless that simply happens to be how
you write.

Do not decide for anyone but yourself, and do not feel you must commit to any
particular move; just write.

Return the entry in the `entry` field, at most {DIARY_SENTENCE_BOUND} sentences.
"""

# The diary user template. A NEW template, so unlike the three shared decision
# templates (``DAY_SPEAK_USER_TEMPLATE`` / ``AI_VOTE_USER_TEMPLATE`` /
# ``MAFIA_POINT_USER_TEMPLATE``, ``.format()``-ed at 16 call sites where a
# missing kwarg is a ``KeyError``) it carries no blast radius and could take any
# slots it liked. It deliberately takes exactly ``REFLECTION_USER_TEMPLATE``'s
# vocabulary instead, so the SAME ``nodes/day.py`` helpers populate it with no
# new plumbing: ``_role_label`` / ``_win_condition_line`` / ``_team_line`` /
# ``_persona_block`` / ``_standings_prompt_block`` / ``_render_context``, plus
# ``{private_thoughts}`` — this player's OWN running private record (spec 028's
# thoughts, and from this spec's second slice its own earlier diaries merged into
# the same slot), the same self-grounding ``_ai_reflect`` does. Never another
# player's: the block is keyed on the acting player's id at the call site.
#
# The invitation's openness lives in ``DIARY_SYSTEM`` (the four moves above), not
# here — this template supplies the grounding and the closing call to write, and
# names no topic and no direction of its own. Its tail does NOT restate the
# sentence bound: ``DIARY_SENTENCE_BOUND`` is interpolated into the system prompt
# only, so there is exactly one place the number is written down.
DIARY_USER_TEMPLATE = """You are {speaker} — your secret role is {role_label}. {win_condition}
{team_line}
{persona}
{standings}The Day has ended, and Night is about to fall.

Recent public discussion:
{context}
{private_thoughts}
Write today's diary entry, in your own voice. Return only the `entry` field,
keeping within the sentence limit you were given.
"""


# ---------------------------------------------------------------------------
# The Moderator's closing story (spec 040)
# ---------------------------------------------------------------------------
# SLICE 4, TASK 4.1 HAS LANDED: ``STORY_SYSTEM`` below is the real prompt, not
# Slice 1's deliberately minimal one. The note that used to stand here said
# "none of those seven is present below" and "there is no record constraint and
# no human clause yet"; it is gone rather than left contradicting the text
# beneath it.
#
# ONE CORRECTION TO THAT RETIRED NOTE, kept because the error is instructive
# rather than clerical. Slice 1's prompt DID carry a record constraint — "Keep
# to the game recorded below." — so "there is no record constraint" was not
# quite true. What it lacked was move 5's NOUN-BOUNDARY form, and that is the
# whole difference §2.6 turns on: a bare "keep to the record" is precisely the
# instruction §2.6 says CREATES the flat summary, which is why the grounding
# and anti-recap halves had to be designed together. So Slice 1 shipped the
# grounding half alone, and the two live runs it produced (a flat recap, plus
# Task 4.4's invented Day phase) are what that costs. The human-clause half of
# the retired note was accurate.
#
# THE SEVEN MOVES, IN THE ORDER THEY APPEAR BELOW (spec 040 §2.6). Each is one
# paragraph, so a maintainer can see which sentence is which move and Slice 4
# Task 4.3 can assert them separately:
#
#   1. NAME THE READER AND WHAT THEY ALREADY KNOW ("They have just read those
#      facts…"). The strongest anti-recap move, because it makes the recap
#      *redundant to a stated audience* rather than merely discouraged.
#   2. NAME WHAT THEY HAVE NEVER SEEN, AND THAT IT IS THE SUBSTANCE ("What they
#      have never seen…"). Deliberately does NOT re-explain diary-vs-reflection
#      mechanics: ``endgame:STORY_PRIVATE_LEGEND`` already states those inside
#      the block itself, and duplicating them here is how the two drift.
#      "WHATEVER OF IT THERE IS" IS LOAD-BEARING, NOT A HEDGE: the private
#      material CAN be empty — ``endgame:STORY_PRIVATE_EMPTY`` renders
#      "(nothing was written privately this game)", and Task 4.4's Game A
#      reached the ending with no Day ever played, so no diary and no
#      reflection existed. An unqualified "this is the substance" would then
#      name an absent substance, which is an invitation to fill the void —
#      Task 4.4's fourth fabrication category exactly. The move is still made
#      in full; the clause just stays true in both cases.
#   3. GRANT THE CHOICE OF THREAD ("Follow one thread…"). 039's lesson, and the
#      shape is lifted from ``DIARY_SYSTEM``'s "some people use a diary to look
#      back… none of them is asked of you": naming ONE direction steers every
#      story the same way, whereas naming the CHOICE makes variation
#      legitimate. The alternatives are a menu to pick ONE from — see the
#      DELIBERATELY ABSENT note below for why that is not the forbidden
#      checklist.
#   4. FORBID THE FIXED FORMS BY NAME ("There is no set form…"). Chronology and
#      roll-call are named explicitly because they are the two moulds a
#      GROUNDED storyteller falls into unprompted — the same reason 039 had to
#      name "summing-up-followed-by-a-plan". Headings, bullets and numbering
#      follow 039's clause.
#   5. THE RECORD CONSTRAINT AS A BOUNDARY ON NOUNS ("Every player you name…").
#      Constraining WHAT MAY BE NAMED rather than HOW IT MAY BE TOLD is what
#      stops the constraint from becoming a summary instruction — §2.6 calls
#      this the sentence the acceptance criterion tests for, so it must stay a
#      real sentence in the text and the "; within that, the telling is yours"
#      tail is part of it, not decoration. Its second sentence carries the
#      functional spec's own wording ("not to invent events, players or
#      motives").
#
#      ITS THIRD SENTENCE IS TASK 4.2'S ("How the game ended is stated below
#      and is not yours to settle…"), and it lives HERE — inside move 5's
#      paragraph — because the gap it closes is a gap in move 5 itself. The
#      noun boundary enumerates three noun classes (players, deaths, votes) and
#      the second sentence three more (events, players, motives); A WINNING
#      SIDE IS NONE OF THOSE SIX. So a story that wrote "and so the Mafia's
#      grip closed on the town" on an unresolved game would satisfy every word
#      of the first two sentences while asserting an outcome the record denies.
#      The "not yours to settle" phrasing deliberately mirrors "the telling is
#      yours" one sentence earlier, opposite in polarity, so the pair reads as
#      one boundary: the telling is free, the ending is fixed. See the TASK 4.2
#      note below for why this is stated actively rather than left passive, and
#      for the audit of every other outcome reference.
#   6. THE HUMAN CLAUSE, AS ITS OWN SENTENCE ("One seat on the roster below…"),
#      so it is separately assertable. It references the MARKED SEAT rather
#      than "the reader" in the abstract — Task 3.3 shipped
#      ``endgame:STORY_ROSTER_HUMAN_MARKER`` and applies it in
#      ``_story_roster_block``, which is what gives this clause something to
#      point at. Live evidence for why it exists: a real run narrated the
#      reader in the third person as "Alexey, another innocent citizen".
#
#      WHY THE MARKER LITERAL IS NOT INTERPOLATED HERE, AND DO NOT "FIX" IT BY
#      RETYPING IT. ``STORY_ROSTER_HUMAN_MARKER``'s own comment predicts that
#      Slice 4 will assert on the exact string, but it cannot be read from this
#      module: ``prompts.py`` is a LEAF (its only import is
#      ``__future__.annotations``) and ``nodes/endgame.py`` imports from it, so
#      importing the marker back would be a cycle. The two remaining options
#      were retyping the literal — exactly what that comment forbids — or
#      relocating the constant, which is Task 3.3's deliverable and not this
#      task's to move. So the clause describes the mark instead of quoting it,
#      which is unambiguous against a roster line that carries no other
#      annotation of that shape. The pairing Task 4.1's brief asks for is a
#      TEST-level act and belongs to Task 4.3: assert this clause in
#      ``STORY_SYSTEM`` and assert ``STORY_ROSTER_HUMAN_MARKER`` in the
#      composed roster, together.
#   7. THE LENGTH BOUND, STATED ONCE ("Return the story in the `text` field…"),
#      interpolated from ``STORY_PARAGRAPH_BOUND`` — see that constant.
#
# DELIBERATELY ABSENT — DO NOT TIDY THIS IN. Three things are missing on
# purpose, and each would look like an improvement in isolation:
#
#   * ANY ENUMERATION OF WHAT TO COVER. An enumeration IS the checklist, and
#     the checklist is the collapse — it is what 039 fought and what would
#     turn this prompt back into the fact sheet the story exists to escape.
#     Move 3's alternatives are NOT that: a checklist says "cover A, B and C",
#     a choice says "follow A, or B, or C — and none of them is asked of you".
#     ``DIARY_SYSTEM`` above draws the same line, and its own DELIBERATELY
#     ABSENT note is the precedent.
#   * ANY WORD LIKE *DRAMATIC*, *VIVID* OR *COMPELLING*. They invite
#     embellishment, which is the risk this spec is most exposed to: §2.7
#     records that embellishment of a real event is one of the three things
#     grounding CANNOT be guaranteed against, and there is deliberately no
#     automated grounding check to catch it.
#   * A PERSONA FOR THE MODERATOR, who has never had one. Every other voice in
#     the game is characterised (spec 016's personas, 024's role guidance);
#     this one is the game's narrator and stays uncharacterised.
#
# ===========================================================================
# TASK 4.2 — THE OUTCOME, AND WHY IT IS STATED ACTIVELY (2026-09-08)
# ===========================================================================
# ``winner`` can be ``"runaway"`` (spec 023's Day cap) and defensively
# ``"draw"``, and §2.6 requires that the prompt not assume a winning side — a
# story declaring a winner on an unresolved game is the most conspicuous
# fabrication available to it. THE PASSIVE HALF WAS ALREADY DONE when this task
# opened, by Tasks 3.3 and 4.1 rather than by anything left over: move 1 says
# "how the game ended" and never "who won", and ``STORY_USER_TEMPLATE``'s first
# slot label is literally "How the game ended: {outcome}". So the question this
# task actually had to answer was whether NOT ASSUMING a winner is the same as
# FORBIDDING one. It is not, and move 5's third sentence is the difference.
#
# THE ARGUMENT THAT DECIDED IT IS STRUCTURAL, NOT BEHAVIOURAL — and the honest
# version matters, because the behavioural case is weaker than it looks. Move
# 5's two existing sentences bound six noun classes (players, deaths, votes;
# events, players, motives) and a WINNING SIDE is none of them, so the
# instruction as it stood PERMITTED the one fabrication §2.6 singles out as the
# most conspicuous. §2.7 is explicit that the instruction is the only thing
# this feature can guarantee ("the instruction itself is directly testable",
# and there is deliberately no automated grounding check), so a gap in the
# instruction is the whole of the exposure. That is why one sentence was added.
#
# WHAT WAS *NOT* THE REASON, recorded because the dispatch brief offered it and
# it does not survive checking. The brief argued from Task 4.4's live
# observation — a story asserting "the final night's killer was REVEALED to be
# one of these two" in a game with zero votes and zero executions — that "a
# model that invents a reveal from an absence will invent a winner from an
# absence". BOTH live fabrications were VOID-FILLS, not overrides of an
# explicit negative: Slice 1's template carried no vote record and no public
# history at all, so nothing in either prompt said "no vote was ever held" or
# "no Day was ever played". The runaway is not that shape. ``_winner_line``
# renders a whole negative SENTENCE for it ("The game did not resolve and was
# stopped at the Day cap (runaway / unresolved game)."), and — measured, see
# the audit below — that sentence reaches the model TWICE. So the model is told
# in words, twice, that the game did not resolve. Predicting an override from
# two void-fills would be a real inference gap. The instruction gap is not.
#
# SHAPE CONSTRAINTS THE SENTENCE HAD TO MEET, and they are why it is phrased
# the way it is. It is ONE sentence, and OUTCOME-SHAPED rather than
# CASE-SHAPED: it names neither "runaway" nor "draw" and reads identically
# whoever won, because a per-outcome branch in the prompt text would be exactly
# the checklist shape 039 fought and the DELIBERATELY ABSENT note above
# forbids. Its conditional is over THE RECORD, not over a case, which is what
# keeps it a single rule ("defer to the record") rather than a switch. It also
# happens to cover ``_winner_line``'s fifth branch — see the audit.
#
# ==== THE AUDIT OF EVERY OUTCOME REFERENCE IN THE STORY PATH ====
# Five references reach the model, all verified by reading the code and the
# last one by rendering it:
#
#   1. ``STORY_SYSTEM`` move 1 — "how the game ended, who died, and who every
#      player really was". Names no side. Correct as written.
#   2. ``STORY_SYSTEM`` move 5, third sentence — this task's addition.
#   3. ``STORY_USER_TEMPLATE``'s first slot label — "How the game ended:".
#      Names no side. Correct as written (it is §2.6's own prescribed phrasing,
#      "how the game ended, below").
#   4. The ``{outcome}`` VALUE — ``endgame:_winner_line(state["winner"])``,
#      REUSED so the story's outcome and the facts message's cannot drift. Five
#      branches, and only two of them name a side: ``ENDGAME_WINNER_LAW`` and
#      ``ENDGAME_WINNER_MAFIA`` do; ``ENDGAME_WINNER_DRAW`` names a resolution
#      without a side; ``ENDGAME_WINNER_RUNAWAY`` names a NON-resolution; and
#      the defensive ``winner is None`` fall-through renders "The game has
#      ended.", which names NOTHING — a genuine void about the outcome, not
#      merely a missing side. That fifth branch is unreachable in practice
#      (every edge into ``end_screen`` sets a winner first) but it is the one
#      case where the material would give the model nothing at all, and move
#      5's new sentence covers it for free, since a tautology names no winning
#      side either.
#   5. **THE FACTS MESSAGE ITSELF, re-entering through ``{context}`` — NOT A
#      REFERENCE ANYONE PLANNED, and reported rather than fixed because the
#      slot is Task 3.3's and the measurement is Task 4.4's.** See the note at
#      the ``_render_context`` call in ``endgame:_ai_story``, which carries the
#      measurement. For THIS task it is benign and mildly helpful: the outcome
#      sentence lands a second time in the tail-most public position, so the
#      runaway's negative is stated twice rather than once. For §2.6's central
#      thesis it is the opposite of helpful, which is why it is written down.
#
# WHAT THIS SLICE STILL DOES **NOT** DO, so the next reader does not think it
# is done:
#
# - **Task 4.3 owns the tests.** No test asserts on this text yet — not on the
#   seven moves, and not on move 5's new third sentence. What to assert for
#   this task: that the composed prompt contains no winning-side claim on a
#   ``winner="runaway"`` game, and that the non-assumption sentence is present
#   as its own sentence (assert the sentence, not the word "runaway" — the
#   sentence deliberately does not contain it).
# - **Task 5.2 owns the ENFORCEMENT pair** (``STORY_MAX_PARAGRAPHS`` and
#   ``STORY_MAX_CHARS``, both in ``nodes/endgame.py`` with the clamp). Only the
#   STATED bound is here — see ``STORY_PARAGRAPH_BOUND``.

# The paragraph bound the prompt STATES, interpolated into ``STORY_SYSTEM``
# exactly once so the prose and the number cannot drift.
#
# PLACEMENT IS A DECISION, NOT A DEFAULT, and Task 5.2 supplies the reason: the
# 039 precedent it cites is SPLIT, and the split is the discipline rather than
# an accident. ``DIARY_SENTENCE_BOUND = 6`` (this file, above ``DIARY_SYSTEM``)
# is the bound STATED in the prompt; ``DIARY_MAX_CHARS = 900``
# (``nodes/day.py``) is the ENFORCEMENT clamp. By that discipline this constant
# — the number the prompt states — belongs with the prompt, and Task 5.2's
# ``STORY_MAX_PARAGRAPHS`` / ``STORY_MAX_CHARS`` belong with the clamp in
# ``nodes/endgame.py``.
#
# WHY TASK 4.1 DEFINES IT RATHER THAN WAITING FOR TASK 5.2: move 7 interpolates
# it, so the consumer cannot be written without the provider. The same
# consumer-before-provider ordering already forced Tasks 1.1 and 1.2 to be
# swapped on this spec. The alternative — hard-coding "four" in the prose with
# a note — is what Task 5.2's line warns against in so many words, since a
# "temporary" literal with no owner is how one becomes permanent.
#
# A DRIFT RISK THE 039 PRECEDENT DOES NOT CARRY, flagged here by Task 4.1 and
# CLOSED BY DERIVATION IN TASK 5.2: 039's two numbers are in different
# DIMENSIONS (6 sentences stated, 900 characters enforced), so neither can
# silently disagree with the other. Here ``STORY_PARAGRAPH_BOUND`` and
# ``STORY_MAX_PARAGRAPHS`` are the SAME number in the SAME dimension in two
# modules. Of the two options this note offered — derive, or pin the equality
# with a test — Task 5.2 took the first: ``endgame:STORY_MAX_PARAGRAPHS`` is
# ``= STORY_PARAGRAPH_BOUND``, imported, so the two cannot disagree at all
# rather than being caught disagreeing afterwards. **So this constant now has
# a second reader in another module**: changing the number here changes what
# ``endgame:_clamp_story`` enforces, which is the intent — enforcing fewer
# paragraphs than the prompt requests would clip a compliant story, and
# enforcing more would not enforce the stated bound.
#
# The STATED bound is only a request — the functional spec requires the length
# to hold for the story actually returned, which is Slice 5's clamp, not this
# wording ("a limit stated only in the request is a request").
STORY_PARAGRAPH_BOUND = 4

STORY_SYSTEM = f"""You are the Moderator of Graphia, a Mafia-style
social-deduction game. The game is over and the closing facts have already been
read out. Tell the one person at the table the story of the game they just
played.

They have just read those facts: how the game ended, who died, and who every
player really was. They do not need any of that again, and telling it back to
them is the one thing this story cannot be.

What they have never seen is in the material below: what the other players
wrote down privately and never said out loud. That private writing — whatever
of it there is — is the substance of this story.

Follow one thread through it. Which thread is yours to choose: one player's
private turn of mind, a suspicion that was right too early, a conspiracy nobody
ever put a name to, a moment that settled the game while no one noticed. None
of those is asked of you, and they are not the only ones — take whichever
thread the record below actually gives you.

There is no set form, and two forms in particular are wrong here: do not walk
through the game day by day, and do not go down the roster player by player. No
headings, no bullet points, no numbering.

Every player you name, every death and every vote you mention must appear
below; within that, the telling is yours. Do not invent events, players or
motives that no one recorded. How the game ended is stated below and is not
yours to settle: where the record names no winning side, neither do you.

One seat on the roster below is marked as the person you are telling this to;
nothing that seat privately thought was ever recorded, so speak of it only by
what it said and did in the open, and never claim to know what the person was
thinking.

Return the story in the `text` field, as at most {STORY_PARAGRAPH_BOUND} short
paragraphs separated by blank lines.
"""

# The story user template — five slots, and NONE of them is filled by a helper
# written for this prompt alone (spec 040 §2.2):
#
#   ``{outcome}``        ``endgame:_winner_line``, REUSED so the story's outcome
#                        and the facts message's cannot drift.
#   ``{roster}``         ``endgame:_story_roster_block`` — name, side, fate, the
#                        ``(the person playing)`` marker on the human seat, and
#                        each AI's persona read through ``_persona_field``.
#   ``{events}``         ``endgame:_format_kill_line`` per ``kill_log`` record,
#                        REUSED, so a death reads the same in both messages.
#   ``{context}``        ``nodes.day:_render_context`` over the public history,
#                        called with a ``speaker_id`` matching no player so
#                        EVERY whisper is dropped (a privacy requirement — the
#                        human's role reveal and the Mafia intros must never
#                        enter a prompt whose output the player reads).
#   ``{private_record}`` ``endgame:_story_private_block`` over the shared cursor
#                        walk — the two channels no player has ever seen.
#
# THE SLOT ORDER IS DELIBERATE AND IT IS NOT §2.2'S TABLE ORDER. The private
# material goes LAST, immediately before the instruction, because it is the
# thing the story is meant to be built from ("the story earns its place from the
# private material") and the tail of a prompt is the part a model weighs most.
# The public history sits in the middle for the same reason inverted: it is the
# largest block, the only one that gets trimmed, and it is there to be checked
# against rather than retold. Do not reorder these to match the table.
#
# THE TWO SLOT LABELS ARE THIS TEMPLATE'S, NOT THE BLOCKS' OWN. The private
# block's first line is ``endgame:STORY_PRIVATE_LEGEND``, which states the two
# things the material cannot claim (no cross-player order; a reflection carries
# no Day) — the label above it says only what the section IS, so the legend is
# not duplicated. The label still has to exist, because the empty case renders
# ``STORY_PRIVATE_EMPTY`` with no legend at all and a bare bullet under nothing
# would be unattributed.
#
# THE OUTCOME SLOT LABEL IS LOAD-BEARING AND SHOULD STAY: it is introduced as
# "How the game ended", not as "The winner was", because ``winner`` can be
# ``"runaway"`` and a story declaring a winner on an unresolved game is §2.6's
# most conspicuous fabrication. It is reference 3 of the five-reference audit
# above ``STORY_SYSTEM`` (Task 4.2), which is where the whole outcome argument
# lives — including why the passive label is not, on its own, sufficient.
STORY_USER_TEMPLATE = """How the game ended: {outcome}

The players:
{roster}

What happened:
{events}

What was said out loud, oldest first:
{context}

What no one else ever saw:
{private_record}

Tell the story of this game.
"""
