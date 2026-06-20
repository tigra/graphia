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

DAY_SPEAK_USER_TEMPLATE = """You are {speaker} — your secret role is {role_label}. {win_condition}
{team_line}
{persona}
{standings}Alive players at the table (name: id):
{roster}

Recent public discussion:
{context}

Never publicly reveal your secret role or your teammates.

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
