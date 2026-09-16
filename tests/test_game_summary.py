"""Pure, terminal-free unit tests for the per-game outcome derivation (spec 045).

Covers the pure layer of tech-spec §2.1 and the six rules of §2.2 — the
``# ===`` section Slice 1 tasks 1.1/1.2 added to :mod:`graphia.eval_ledger`
beside the locate/list/read layer: :class:`graphia.eval_ledger.GameSummary`,
:func:`graphia.eval_ledger.summarise_game`, and the two side ids
:data:`~graphia.eval_ledger.WINNER_MAFIA` / ``WINNER_LAW_ABIDING`` its
``winner`` names. No Textual, no ``run_test()``, no ``App``, no widget: this
module imports three production modules and the stdlib.

**Which ``GameSummary`` this is.** Two unrelated classes carry that name in this
repo, and they sit beside each other in ``tests/``. This module is about
:class:`graphia.eval_ledger.GameSummary` — *one preserved eval transcript's
shape*, derived by reading the committed text: the Days it played, the
survivors of each side, and the winning side. It is **not**
:class:`graphia.stats_store.GameSummary`, which long predates it and is one
finished game's career-stats delta (``human_role`` / ``outcome`` / ``rounds`` /
``votes_called`` …), consumed by ``career_events.py``,
``tests/test_career_stats.py``, ``tests/test_career_event_store.py``,
``tests/test_abandoned_game.py`` and ``infra/lambda/career_consumer/``. Every
import below therefore names its module explicitly; a bare
``from … import GameSummary`` anywhere in this file would be ambiguous to a
reader and is deliberately avoided.

**Why the split, in one measured number.** ``tests/test_ledger_viewer.py`` runs
at ≈0.63 s per ``run_test()`` item; the terminal-free modules run at ≈0.007 s.
That is ~100x per item, and it is the whole reason this file exists rather than
these cases living in section B16 of the viewer's suite (tech-spec §4). This
module plays for the *derivation* exactly the role
``tests/test_transcript_highlight.py`` plays for the tokenizer and
``tests/test_transcript_search.py`` plays for the matchers.

The rule that decides placement, restated so a later reader does not have to
re-derive it: **"what happened in this game" is not a Textual fact.** How many
Days it played, who was left standing and which side won are statements about a
string. Which colour the winning count is painted, whether it stays legible
under the list's own highlight, and where the header sits are the widget's, and
they are asserted in ``tests/test_ledger_viewer.py``'s B16.

--- What is here ---

1. **The winner sentence, in both eras.** The corpus spans three transcript
   eras; the pre-022 one writes ``  Moderator: The Mafia have won.`` indented
   behind a speaker prefix while every later era writes it flush-left, so the
   match must be an **unanchored substring** test. Ten real finished games live
   in that older shape, so a line-anchored match would lose them silently. The
   era pair is asserted here *with a non-vacuity precondition beside it* — the
   line-anchored answer really is wrong on this input — so a future
   "tightening" to ``^`` fails loudly instead of quietly blanking ten games.

2. **The runaway and the draw sentence**, both of which map to ``winner =
   None``. Neither occurs **anywhere** in the committed corpus (0 occurrences of
   each, measured 2026-09-15), so no corpus oracle will ever exercise them and
   these synthetic cases are the only thing that does. They are also the reason
   ``winner = None`` is not a synonym for "no ending": a runaway game has real
   final standings and no winning side, and the row must show both facts.

3. **The three numbers on a well-formed game**, in both eras and for both
   outcomes, plus the two traps the tech spec calls out by name: ``days``
   counts Days **played**, so a game that ends in its fourth Night reads 3; and
   the in-game ``<recap>`` standings are **not** final standings, so a fixture
   whose last recap reports the pre-kill counts must still derive the post-kill
   ones.

4. **The two absence levels, which are different states.** ``None`` (no Day
   markers at all — empty, unreadable, truncated) versus a summary carrying
   ``days`` with the other three ``None`` (the game played Days but records no
   ending — ten real games, across two June-2026 runs). A row shows those two
   differently, so a test asserts they are *distinguishable*, not merely that
   each is handled.

5. **The defensive posture**, mirroring ``_dig``: a malformed roster line, a
   death bullet with no readable role, and arithmetic that would go negative all
   resolve to ``None`` standings rather than raising or rendering a nonsense
   count — and the two counts always travel together, so a row never shows one
   real number beside an invented one.

6. **The anti-vacuity test for the death-bullet discriminator** — the single
   most important test in this module. The ``Who they really were:`` persona
   reveal emits ``• Name (Role) — …`` bullets with **no** cycle prefix; the
   ``Night N:`` / ``Day N:`` prefix is the *only* thing that tells a death from
   a persona. The fixture is built so that dropping the prefix requirement
   reads the game as ``0-0`` — a plausible-looking answer, which is what makes
   it dangerous — and a precondition test asserts that the prefix-free rule
   really does miscount *this* text, so the pair cannot pass vacuously.

7. **The writer-pinning test.** ``eval_ledger`` keeps its **own** literals for
   the winner sentences, the roster header and the two role labels, so the
   model-free viewer never imports the model-facing prompt stack. That
   independence is bought with a coupling nothing else would notice, so each
   literal is pinned here to the constant that writes it. A reworded sentence
   must fail **here**, loudly, rather than silently blanking the winner on every
   row of every run. (``ENDGAME_HEADER_KILLS`` is **not** pinned as a rule
   literal: the derivation deliberately does not use it — a death bullet is
   recognised by its own shape, not by the section header above it — so it
   appears below only as a fixture-shape precondition.)

8. **The four corpus oracles** — the ledger as oracle, the win rule, the
   recorded lineup and day-count agreement — swept over the committed store
   under ``evals/``, one parametrised item per run directory, behind the
   ``_requires_corpus`` skip gate and strictly **read-only**. Everything above
   asks whether the derivation is right on text this file wrote; the oracles
   ask whether the six rules survive hundreds of real games across three
   transcript eras. The first of the four is the strongest test in this module:
   it checks the parser's per-game winners against a tally the eval harness
   wrote at **measurement** time, from the live game state, with no transcript
   in the loop.

--- What is deliberately NOT here ---

* **Golden corpus counts.** No assertion in this file names 583, 573, 41 or 39.
  Count-shaped assertions stay relative, following
  ``tests/test_transcript_highlight.py``'s convention — the corpus gains a
  directory on every eval run, so a pinned number is a test that fails on a
  green tree.
* **Any cross-check against the in-game ``<recap>`` standings over the corpus**
  (tech-spec §2.2/§4): an era-blind one gives 24 false disagreements in 1,278,
  because the June era posted its recap *after* that Day's execution. Item 3's
  recap case is the opposite assertion — that the recap is **ignored** — on one
  synthetic game whose true answer is checkable by eye.
* **Colour, weight, contrast, geometry, snapshots and timings.** All widget
  facts, all asserted in ``tests/test_ledger_viewer.py``; ``snap_compare``
  appears in zero test files in this repo and there are no wall-clock
  assertions anywhere in the suite.

--- Offline by construction ---

:mod:`graphia.eval_ledger` is the pure, Textual-free, **model-free** layer, so
the autouse ``safe_llm`` net in ``conftest.py`` is simply irrelevant here,
exactly as it is for ``tests/test_ledger_model.py``,
``tests/test_transcript_highlight.py`` and ``tests/test_transcript_search.py``.
Every input to the synthetic sections is a module-level string written by hand.
The corpus oracles of section 7 do read from disk — that is what an oracle over
committed data *is* — and they are **strictly read-only**: they open the
preserved games and ``evals/blunder-ledger.yaml`` for reading and write nothing,
anywhere, ever. Nothing under ``evals/`` is created, moved, modified or deleted
by this module, which matters more here than the usual amount: that directory is
the project's only long-term measurement record, and a test that could damage it
would be a worse bug than any it could catch. The two writer modules this file
imports (:mod:`graphia.prompts` and :mod:`graphia.tools.eval_transcript`) are
imported for their **constants**; no model, no network, no AWS.

The real production symbols are imported directly (never re-implemented
locally), so a rename breaks these tests — which is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from graphia import eval_ledger
from graphia.eval_ledger import (
    WINNER_LAW_ABIDING,
    WINNER_MAFIA,
    GameSummary,
    LedgerParseError,
    RawRecord,
    load_ledger,
    read_transcript,
    summarise_game,
    transcript_dir_for,
)
from graphia.prompts import (
    ENDGAME_HEADER_KILLS,
    ENDGAME_HEADER_ROSTER,
    ENDGAME_PERSONA_HEADER,
    ENDGAME_WINNER_DRAW,
    ENDGAME_WINNER_LAW,
    ENDGAME_WINNER_MAFIA,
    ENDGAME_WINNER_RUNAWAY,
)
from graphia.tools.eval_transcript import _ROLE_LABELS

# ===========================================================================
# The synthetic games
# ===========================================================================
#
# Hand-written, never lifted from a real preserved game, so every expected
# number below is checkable by eye from the fixture itself: count the ``<day>``
# lines, count the roster's parenthesised labels, subtract the death bullets.
# The corpus is task 1.4's job, and it answers a different question — whether
# the rules survive hundreds of files no fixture author would think to write —
# not whether the arithmetic is right.
#
# They are nevertheless *writer-shaped*: each carries the header line, the
# ``<transcript>`` wrapper, the tag/indentation dialect of the era it stands
# for, and the endgame block in the writer's own order (winner sentence, kills
# header, death bullets, roster line, persona reveal). Preconditions further
# down assert that shape rather than trusting it.

# --- The spec-022+ era: flush-left tags, flush-left endgame ----------------

# A Mafia win. 1 Day played, roster 3 Law-abiding + 2 Mafia, 2 Night kills,
# so the final standings are 1-2. Its LAST recap reports ``2 Law-abiding
# Citizens and 2 Mafiosos remain`` — the pre-kill counts, which is exactly why
# the derivation must not read the standings off a recap.
_MODERN_MAFIA_WIN = """\
Game 1 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=2

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Mira" role="Law-abiding Citizen" />
<player name="Nadia" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
<player name="Kenzo" role="Mafia" />
</setup>
<night>
Night 1 begins.
<kill>Avery — Law-abiding Citizen</kill>
</night>
<day>
Day 1 begins.
<round>
Round 1.
Mira: Avery is gone. Someone at this table did it.
Kenzo: A terrible loss. We should be careful who we accuse.
<recap>Day 1, 9 PM status: 2 Law-abiding Citizens and 2 Mafiosos remain. No execution votes called yet today.</recap>
</round>
</day>
<night>
Night 2 begins.
<kill>Nadia — Law-abiding Citizen</kill>
</night>
<endgame>
The Mafia have won.

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Night 2: Nadia (Law-abiding Citizen)

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Nadia (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)

Who they really were:
• Mira (Law-abiding Citizen) — bold and confrontational; speaks in rapid fire. Mira organises the town's meetings.
• Amara (Mafia) — publicly presented as a patient negotiator (shrewd and calculating) … but was really a Mafioso: a lieutenant of the family.
</endgame>
</transcript>
"""

# A Law-abiding win, shaped like the real game 5 of the 2026-09-14 local run
# that functional-spec §2.1 uses as its golden: 2 Days played, ending 4-0.
# Roster 5 Law-abiding + 2 Mafia; one Night kill and two executions.
_MODERN_LAW_WIN = """\
Game 5 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=2

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Mira" role="Law-abiding Citizen" />
<player name="Nadia" role="Law-abiding Citizen" />
<player name="Leif" role="Law-abiding Citizen" />
<player name="Hiroshi" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
<player name="Kenzo" role="Mafia" />
</setup>
<night>
Night 1 begins.
<kill>Avery — Law-abiding Citizen</kill>
</night>
<day>
Day 1 begins.
<round>
Round 1.
Mira: Amara has answered nothing all morning.
<vote initiator="Mira" target="Amara" outcome="executed" />
</round>
</day>
<night>
Night 2 begins.
Pointing round 1:
Kenzo points at Mira
</night>
<day>
Day 2 begins.
<round>
Round 1.
Leif: Kenzo covered for Amara twice. That is the whole case.
<vote initiator="Leif" target="Kenzo" outcome="executed" />
</round>
</day>
<endgame>
The Law-abiding Citizens have won.

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Day 1: Amara (Mafia) executed
• Day 2: Kenzo (Mafia) executed

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Nadia (Law-abiding Citizen), Leif (Law-abiding Citizen), Hiroshi (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)

Who they really were:
• Mira (Law-abiding Citizen) — bold and confrontational; speaks in rapid fire. Mira organises the town's meetings.
• Kenzo (Mafia) — publicly presented as a prickly craftsman (short-tempered and curt) … but was really a Mafioso: an enforcer.
</endgame>
</transcript>
"""

# --- The pre-022 era: every Moderator line indented behind a speaker prefix -
#
# ``<day>`` / ``<night>`` are still flush-left in this era (which is why the
# day rule is era-safe), but the winner sentence is NOT: it arrives as
# ``  Moderator: The Mafia have won.``. ``<endgame>`` does not exist here at
# all — the endgame block simply follows the last Moderator line inside the
# final ``<night>``.

# 2 Days played, ending in the THIRD Night, so ``days`` must read 2 and not 3.
# Roster 5 Law-abiding + 2 Mafia, three Night kills, so the standings are 2-2 —
# the parity the Mafia win on, and the shape most preserved games end in.
_PRE_022_MAFIA_WIN = """\
Game 1 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
  Avery — Law-abiding Citizen
    (no persona recorded)
  Jax — Law-abiding Citizen
    Personality: honest and straightforward
  Lincoln — Mafia
    Personality: quiet and methodical
  Sanaa — Mafia
    Personality: calm and deliberate
  Ahmed — Law-abiding Citizen
    Personality: honest and ordinary
  Haniya — Law-abiding Citizen
    Personality: honest and ordinary
  Mira — Law-abiding Citizen
    Personality: calm and observant
</setup>
<night>
  Night 1 begins.
  Moderator: During the night, Haniya was killed.
</night>
<day>
<round>
  Moderator: Day 1 begins.
  Jax: We lost Haniya. Nobody saw anything.
</round>
</day>
<night>
  Night 2 begins.
  Moderator: During the night, Avery was killed.
</night>
<day>
<round>
  Moderator: Day 2 begins.
  Mira: Two nights, two of us gone, and not one accusation has stuck.
</round>
</day>
<night>
  Night 3 begins.
  Moderator: During the night, Ahmed was killed.
  Moderator: The Mafia have won.

Events this game:
• Night 1: Haniya (Law-abiding Citizen)
• Night 2: Avery (Law-abiding Citizen)
• Night 3: Ahmed (Law-abiding Citizen)

Full roster: Avery (Law-abiding Citizen), Jax (Law-abiding Citizen), Lincoln (Mafia), Sanaa (Mafia), Ahmed (Law-abiding Citizen), Haniya (Law-abiding Citizen), Mira (Law-abiding Citizen)

Who they really were:
• Jax (Law-abiding Citizen) — honest and straightforward. Jax is a retired school librarian who tends his garden.
• Lincoln (Mafia) — publicly presented as a retired superintendent (quiet and methodical) … but was really a Mafioso: a trusted lieutenant.
</night>
</transcript>
"""

# The other pre-022 outcome: 2 Days, roster 3 Law-abiding + 2 Mafia, one Night
# kill and two executions, ending 2-0.
_PRE_022_LAW_WIN = """\
Game 4 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
  Avery — Law-abiding Citizen
  Jax — Law-abiding Citizen
  Mira — Law-abiding Citizen
  Lincoln — Mafia
  Sanaa — Mafia
</setup>
<night>
  Night 1 begins.
  Moderator: During the night, Avery was killed.
</night>
<day>
<round>
  Moderator: Day 1 begins.
  Mira: Lincoln has an answer for everything and evidence for nothing.
  Moderator: Lincoln has been executed.
</round>
</day>
<night>
  Night 2 begins.
  Moderator: The night passed with no one killed.
</night>
<day>
<round>
  Moderator: Day 2 begins.
  Jax: Sanaa spoke for Lincoln every single time. That is the case.
  Moderator: Sanaa has been executed.
  Moderator: The Law-abiding Citizens have won.

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Day 1: Lincoln (Mafia) executed
• Day 2: Sanaa (Mafia) executed

Full roster: Avery (Law-abiding Citizen), Jax (Law-abiding Citizen), Mira (Law-abiding Citizen), Lincoln (Mafia), Sanaa (Mafia)

Who they really were:
• Jax (Law-abiding Citizen) — honest and straightforward. Jax is a retired school librarian.
</round>
</day>
</transcript>
"""

# --- The two side-less endings, neither of which occurs in the corpus -------

# The whole-game Day-cap hit (spec 023). It carries a full endgame block — a
# winner sentence naming a NON-resolution, real death bullets and a real roster
# line — so its standings are genuinely known (2-2) while its winner is None.
# That is what makes it different from a game with no ending at all.
_RUNAWAY_GAME = """\
Game 7 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
</setup>
<night>
Night 1 begins.
<kill>Avery — Law-abiding Citizen</kill>
</night>
<day>
Day 1 begins.
</day>
<night>
Night 2 begins.
<kill>Nadia — Law-abiding Citizen</kill>
</night>
<day>
Day 2 begins.
</day>
<day>
Day 3 begins.
</day>
<day>
Day 4 begins.
</day>
<day>
Day 5 begins.
</day>
<endgame>
The game did not resolve and was stopped at the Day cap (runaway / unresolved game).

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Night 2: Nadia (Law-abiding Citizen)

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Nadia (Law-abiding Citizen), Leif (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)

Who they really were:
• Mira (Law-abiding Citizen) — bold and confrontational. Mira organises the town's meetings.
</endgame>
</transcript>
"""

# The retained draw sentence, kept for ``end_screen``'s defensive
# ``winner == "draw"`` path. Nobody died at all, so the endgame block carries
# the writer's ``• (no kills this game)`` line — a bullet with a cycle-free
# shape AND no role label, which the death rule must not touch.
_DRAW_GAME = """\
Game 9 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
</setup>
<day>
Day 1 begins.
</day>
<day>
Day 2 begins.
</day>
<day>
Day 3 begins.
</day>
<day>
Day 4 begins.
</day>
<day>
Day 5 begins.
</day>
<endgame>
The game ended in a draw at the Day cap.

Events this game:
• (no kills this game)

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Nadia (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)
</endgame>
</transcript>
"""

# --- The two absence levels ------------------------------------------------

# A game that played Days and then simply stops: it hit the Day cap of its era
# and the text ends mid-structure, with no closing announcement and no roster.
# Ten of the preserved games are like this, across two runs of June 2026. Its
# LENGTH is genuine, so it is reported; only its standings are withheld.
_NO_ENDING_GAME = """\
Game 3 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
  Avery — Law-abiding Citizen
  Ila — Law-abiding Citizen
  Adalyn — Mafia
</setup>
<night>
  Night 1 begins.
  Moderator: During the night, Kaiya was killed.
</night>
<day>
<round>
  Moderator: Day 1 begins.
  Ila: The garden was disturbed. Something was torn away.
</round>
</day>
<night>
  Night 2 begins.
  Moderator: During the night, Lumi was killed.
</night>
<day>
<round>
  Moderator: Day 2 status: 3 Law-abiding Citizens and 2 Mafiosos remain.
  Adalyn: I believe the killer left us a clue, small and easily overlooked.
</round>
</day>
</transcript>
"""

# A file that was truncated before the first Day ever opened. Structurally a
# transcript, but it yields no Day marker, so there is nothing to say about it.
_TRUNCATED_BEFORE_ANY_DAY = """\
Game 2 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=10

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
</setup>
<night>
Night 1 begins.
"""

# Not a transcript at all — what a mis-decoded or corrupt file looks like by
# the time ``read_transcript`` has handed it over.
_UNREADABLE_TEXT = "\x00\x01\x02 �� not a transcript \x00 <day\x00>"

# --- The defensive cases ---------------------------------------------------

# The roster line of the modern Mafia-win fixture, named once so the two
# malformed variants below cannot silently stop replacing anything.
_MODERN_ROSTER_LINE = (
    "Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), "
    "Nadia (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)"
)

# A roster line whose entries carry no parenthesised role label. The header is
# there, so the game did end; the line is unreadable, so the standings are
# withheld rather than guessed. The winner is still known — the two facts are
# read by independent rules.
_MALFORMED_ROSTER_GAME = _MODERN_MAFIA_WIN.replace(
    _MODERN_ROSTER_LINE,
    "Full roster: Avery, Mira, Nadia, Amara, Kenzo",
)

# The other malformed shape: entries that ARE parenthesised, but with a role
# vocabulary this game never had.
_UNKNOWN_ROLE_ROSTER_GAME = _MODERN_MAFIA_WIN.replace(
    _MODERN_ROSTER_LINE,
    "Full roster: Avery (Detective), Mira (Sheriff), Nadia (Doctor)",
)

# A death bullet carrying its cycle prefix but no readable role. One death of
# unknown side makes the WHOLE arithmetic untrustworthy — silently skipping it
# would inflate the survivors of whichever side actually lost that player — so
# the standings go to None rather than to a plausible wrong pair.
_UNATTRIBUTABLE_DEATH_GAME = _MODERN_MAFIA_WIN.replace(
    "• Night 2: Nadia (Law-abiding Citizen)",
    "• Night 2: Nadia",
)

# More deaths than players: three Law-abiding deaths against a roster of two.
# Impossible from a well-formed transcript (0 occurrences in the corpus) and a
# sign the text is not what the parser thinks it is, so it withholds rather
# than rendering ``-1``.
_NEGATIVE_ARITHMETIC_GAME = """\
Game 6 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=2

<transcript>
<day>
Day 1 begins.
</day>
<day>
Day 2 begins.
</day>
<endgame>
The Mafia have won.

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Night 2: Nadia (Law-abiding Citizen)
• Night 3: Leif (Law-abiding Citizen)

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Kenzo (Mafia)
</endgame>
</transcript>
"""

# --- The persona trap ------------------------------------------------------
#
# THE fixture of this module. Its persona section names exactly the four
# SURVIVORS — 2 Law-abiding, 2 Mafia — and its death bullets name the two
# Law-abiding players who died. A rule that matched a bare ``• `` would
# therefore count 4 + 2 = 6 deaths against a roster of 6 and report ``0-0``:
# not a crash, not a negative, a believable pair of numbers on every row.
# The true answer is ``2-2``.
_PERSONA_TRAP_GAME = """\
Game 2 | provider=ollama | large_model=qwen3-coder:30b | small_model=qwen2.5:3b | games=2

<transcript>
<setup>
<player name="Avery" role="Law-abiding Citizen" />
<player name="Mira" role="Law-abiding Citizen" />
<player name="Nadia" role="Law-abiding Citizen" />
<player name="Leif" role="Law-abiding Citizen" />
<player name="Amara" role="Mafia" />
<player name="Kenzo" role="Mafia" />
</setup>
<night>
Night 1 begins.
<kill>Avery — Law-abiding Citizen</kill>
</night>
<day>
Day 1 begins.
<round>
Round 1.
Amara: Leif has contradicted himself twice this morning.
<vote initiator="Amara" target="Leif" outcome="executed" />
</round>
</day>
<endgame>
The Mafia have won.

Events this game:
• Night 1: Avery (Law-abiding Citizen)
• Day 1: Leif (Law-abiding Citizen) executed

Full roster: Avery (Law-abiding Citizen), Mira (Law-abiding Citizen), Nadia (Law-abiding Citizen), Leif (Law-abiding Citizen), Amara (Mafia), Kenzo (Mafia)

Who they really were:
• Mira (Law-abiding Citizen) — bold and confrontational; speaks in rapid fire. Mira organises the town's meetings.
• Nadia (Law-abiding Citizen) — stern and commanding; direct and forceful. Nadia is a retired officer.
• Amara (Mafia) — publicly presented as a patient negotiator (shrewd and calculating) … but was really a Mafioso: a lieutenant of the family.
• Kenzo (Mafia) — publicly presented as a prickly craftsman (short-tempered and curt) … but was really a Mafioso: an enforcer.
</endgame>
</transcript>
"""


def _summary(text: str) -> GameSummary:
    """:func:`summarise_game`, narrowed to the non-``None`` case.

    Every caller of this helper is asserting something *about* a summary, so a
    ``None`` there is a different failure from the one under test and is worth
    its own message rather than an ``AttributeError`` three lines later.
    """
    summary = summarise_game(text)
    assert summary is not None, (
        "summarise_game returned None — it found no `<day>` marker at all in "
        "this fixture, so every assertion below is about the wrong thing. "
        "Check the fixture still carries flush-left `<day>` lines."
    )
    return summary


# ===========================================================================
# 1. The winner sentence, in both eras (tech-spec §2.2, rule 5)
# ===========================================================================
#
# The rule: an UNANCHORED substring test. The pre-022 era writes the sentence
# behind a ``  Moderator: `` prefix and every later era writes it flush-left;
# ``<endgame>`` is absent from exactly 20 committed files (the two pre-022
# runs) and 10 of those 20 did finish, carrying a winner, a roster and a kills
# section. Absence of the tag tracks the ERA, not the outcome — which is the
# half a plausible implementation gets wrong, because anchoring the match reads
# like a tightening and costs ten real games.

_WINNER_CASES = [
    # (fixture, expected winner, why)
    (_MODERN_MAFIA_WIN, WINNER_MAFIA, "spec-022+ era, flush-left sentence"),
    (_MODERN_LAW_WIN, WINNER_LAW_ABIDING, "spec-022+ era, flush-left sentence"),
    (_PRE_022_MAFIA_WIN, WINNER_MAFIA, "pre-022 era, `  Moderator: `-prefixed"),
    (_PRE_022_LAW_WIN, WINNER_LAW_ABIDING, "pre-022 era, `  Moderator: `-prefixed"),
    (_RUNAWAY_GAME, None, "a NON-resolution names no side"),
    (_DRAW_GAME, None, "a resolution without a side names no side"),
    (_NO_ENDING_GAME, None, "no ending recorded, so no sentence to read"),
]

_WINNER_CASE_IDS = [
    "modern-mafia",
    "modern-law",
    "pre022-mafia",
    "pre022-law",
    "runaway",
    "draw",
    "no-ending",
]


@pytest.mark.parametrize(
    ("text", "expected", "why"),
    _WINNER_CASES,
    ids=_WINNER_CASE_IDS,
)
def test_each_winner_sentence_names_its_side_in_every_era(
    text: str, expected: str | None, why: str
) -> None:
    """Every shape the writer can end a game in resolves to the right side."""
    summary = _summary(text)
    assert summary.winner == expected, (
        f"winner={summary.winner!r}, expected {expected!r} ({why})"
    )


@pytest.mark.parametrize(
    "text",
    [_PRE_022_MAFIA_WIN, _PRE_022_LAW_WIN],
    ids=["pre022-mafia", "pre022-law"],
)
def test_a_line_anchored_match_really_would_lose_the_pre_022_winner(
    text: str,
) -> None:
    """Non-vacuity precondition for the unanchored rule.

    The era pair above proves nothing unless the anchored answer is genuinely
    different **on these inputs**. It is: in the pre-022 fixtures the sentence
    sits behind two spaces and a ``Moderator: `` prefix, so a ``(?m)^`` match
    finds nothing at all — while the unanchored one finds it. Ten real finished
    games live in exactly that shape.
    """
    alternation = "|".join(
        re.escape(sentence)
        for sentence in (ENDGAME_WINNER_LAW, ENDGAME_WINNER_MAFIA)
    )
    anchored = re.compile(f"(?m)^(?:{alternation})")
    assert anchored.search(text) is None, (
        "This fixture's winner sentence is reachable by a line-anchored match, "
        "so it no longer stands for the pre-022 era and the assertion below "
        "proves nothing. Restore the `  Moderator: ` prefix in front of it."
    )
    assert _summary(text).winner is not None, (
        "summarise_game found no winner in a game that HAS one. The winner "
        "match has become line-anchored; tech-spec 045 §2.2 requires an "
        "unanchored substring test, because the pre-022 era indents the "
        "sentence behind a speaker prefix and 10 finished games are in it."
    )


def test_a_runaway_game_reports_real_standings_beside_its_absent_winner() -> None:
    """``winner = None`` is not a synonym for "no ending".

    A runaway game carries a full endgame block, so its final standings are
    genuinely known — the row shows the numbers and highlights neither side. A
    derivation that treated an unrecognised winner sentence as "nothing here"
    would blank standings it can read.
    """
    summary = _summary(_RUNAWAY_GAME)
    assert (summary.law_abiding, summary.mafia, summary.winner) == (2, 2, None), (
        f"runaway game derived {summary!r}; expected 2 Law-abiding and 2 Mafia "
        "alive with no winning side"
    )


def test_the_no_kills_line_is_not_read_as_a_death() -> None:
    """``• (no kills this game)`` is a bullet, and it is not a death.

    The writer emits it in place of the death list when nobody died. It has no
    cycle prefix AND no role label, so a prefix-free death rule would not merely
    miscount it — it would fail to attribute it and withhold the standings of a
    game whose standings are the easiest of all to derive.
    """
    summary = _summary(_DRAW_GAME)
    assert (summary.law_abiding, summary.mafia, summary.winner) == (3, 2, None), (
        f"draw game with no deaths derived {summary!r}; expected the full "
        "roster alive (3 Law-abiding, 2 Mafia) and no winning side"
    )


# ===========================================================================
# 2. The three numbers on a well-formed game (tech-spec §2.2, rules 1-4)
# ===========================================================================

_FULL_SUMMARY_CASES = [
    # (fixture, expected summary, why)
    (
        _MODERN_MAFIA_WIN,
        GameSummary(days=1, law_abiding=1, mafia=2, winner=WINNER_MAFIA),
        "1 Day; roster 3+2 minus 2 Law-abiding Night kills",
    ),
    (
        _MODERN_LAW_WIN,
        GameSummary(days=2, law_abiding=4, mafia=0, winner=WINNER_LAW_ABIDING),
        "2 Days; roster 5+2 minus 1 Night kill and both Mafiosos executed",
    ),
    (
        _PRE_022_MAFIA_WIN,
        GameSummary(days=2, law_abiding=2, mafia=2, winner=WINNER_MAFIA),
        "2 Days played, ended in Night 3; roster 5+2 minus 3 Night kills",
    ),
    (
        _PRE_022_LAW_WIN,
        GameSummary(days=2, law_abiding=2, mafia=0, winner=WINNER_LAW_ABIDING),
        "2 Days; roster 3+2 minus 1 Night kill and both Mafiosos executed",
    ),
]


@pytest.mark.parametrize(
    ("text", "expected", "why"),
    _FULL_SUMMARY_CASES,
    ids=["modern-mafia", "modern-law", "pre022-mafia", "pre022-law"],
)
def test_a_finished_game_derives_all_three_numbers_and_its_winner(
    text: str, expected: GameSummary, why: str
) -> None:
    """The whole value object at once, for both eras and both outcomes.

    Asserted as a complete :class:`~graphia.eval_ledger.GameSummary` rather than
    field by field: a derivation that got ``days`` right and the standings wrong
    would pass three of four narrower assertions, and the row shows all four
    together.
    """
    assert _summary(text) == expected, f"expected {expected!r} — {why}"


def test_days_counts_the_days_played_not_the_nights() -> None:
    """A game that ends in its third Night reads 2, per functional-spec §2.1.

    Most games end on a Mafia Night kill, which leaves them one Night longer
    than they are Days. The number on the row must be a Day the moderator
    announced out loud, so the reviewer can go and find it.

    The precondition is the point: this fixture really does hold more Nights
    than Days, so a rule that counted ``<night>`` — or that counted either tag —
    would answer 3, not 2.
    """
    nights = _PRE_022_MAFIA_WIN.count("<night>")
    days = _PRE_022_MAFIA_WIN.count("<day>")
    assert nights > days, (
        f"fixture has {nights} Nights and {days} Days; it must hold MORE "
        "Nights than Days or it cannot tell the two rules apart"
    )
    assert _summary(_PRE_022_MAFIA_WIN).days == days, (
        f"days should be the {days} Days played, not the {nights} Nights"
    )


def test_the_in_game_recap_standings_are_not_used() -> None:
    """Roster-minus-deaths, never the last ``<recap>`` (tech-spec §2.2).

    The last recap of a game that ends on a Night kill reports the **pre-kill**
    counts. This fixture's final recap says ``2 Law-abiding Citizens and 2
    Mafiosos remain`` and the true final standings are ``1-2``, so a derivation
    that read the recap would be off by exactly the kill that ended the game —
    on the majority of preserved games, since most of them end that way.
    """
    assert "2 Law-abiding Citizens and 2 Mafiosos remain" in _MODERN_MAFIA_WIN, (
        "the fixture no longer carries the misleading recap, so this test "
        "cannot distinguish the two rules any more"
    )
    summary = _summary(_MODERN_MAFIA_WIN)
    assert (summary.law_abiding, summary.mafia) == (1, 2), (
        f"derived {summary.law_abiding}-{summary.mafia}; the recap's 2-2 is "
        "the PRE-kill count and must not be the source of the standings"
    )


# ===========================================================================
# 3. The two absence levels, and that they are different states
# ===========================================================================
#
# Functional-spec §2.4 gives a game with no recorded ending a day count and a
# dash; a file that cannot be read at all gets its number alone. Those are two
# different rows, so they must be two different return values — and a test that
# only checked each in isolation would pass on an implementation that collapsed
# them.

_NO_SUMMARY_AT_ALL = [
    ("", "empty — what read_transcript returns for any OSError"),
    ("   \n\n\t\n", "whitespace only"),
    (_TRUNCATED_BEFORE_ANY_DAY, "truncated before the first Day opened"),
    (_UNREADABLE_TEXT, "not a transcript at all"),
]


@pytest.mark.parametrize(
    ("text", "why"),
    _NO_SUMMARY_AT_ALL,
    ids=["empty", "whitespace", "truncated", "unreadable"],
)
def test_text_with_no_day_marker_yields_no_summary_at_all(
    text: str, why: str
) -> None:
    """The first absence level: there is nothing to say about this game.

    This is also what makes the defensiveness compose for free:
    ``read_transcript`` already resolves any ``OSError`` to ``""``, and ``""``
    lands here, so an unreadable file reaches the bare-number row with no extra
    branch anywhere.
    """
    assert summarise_game(text) is None, f"expected None for {why}"


def test_a_game_with_days_but_no_ending_keeps_its_days_and_withholds_the_rest() -> None:
    """The second absence level: the length is genuine, the standings are not.

    Ten of the preserved games are like this, across two runs of June 2026 —
    they hit the Day cap of their era and the text simply stops. The row shows
    ``2d  —``: a real day count, and a dash where a pair of numbers would be
    read as final standings.
    """
    summary = _summary(_NO_ENDING_GAME)
    assert summary == GameSummary(
        days=2, law_abiding=None, mafia=None, winner=None
    ), f"derived {summary!r}; expected the 2 Days it played and nothing else"


def test_the_two_absence_levels_are_distinguishable() -> None:
    """A caller can tell "nothing to say" from "no ending recorded".

    They render as two different rows, so collapsing them — returning ``None``
    for both, or a ``days=0`` summary for both — would be invisible to any test
    that looked at each on its own.
    """
    unreadable = summarise_game(_UNREADABLE_TEXT)
    no_ending = summarise_game(_NO_ENDING_GAME)
    assert unreadable is None
    assert isinstance(no_ending, GameSummary)
    assert no_ending.days > 0, (
        "a game with no ending must still report the Days it played; a 0 there "
        "would make it indistinguishable from an unreadable file on the row"
    )


def test_the_standings_are_absent_rather_than_a_misleading_zero() -> None:
    """``0`` is a real outcome; absence must not be spelled as one.

    A Law-abiding win ends with ``mafia = 0`` and that is a fact worth showing.
    So a game whose standings are unknown must report ``None``, never ``0`` —
    otherwise the ten ending-less games would read as clean Law-abiding wins.
    """
    won = _summary(_MODERN_LAW_WIN)
    unknown = _summary(_NO_ENDING_GAME)
    assert won.mafia == 0, "a Law-abiding win really does end with 0 Mafiosos"
    assert unknown.mafia is None, (
        "a game with no recorded ending reported 0 Mafiosos alive, which reads "
        "as a Law-abiding win; it must report None"
    )


# ===========================================================================
# 4. The defensive posture (tech-spec §2.2, "defensive posture")
# ===========================================================================
#
# Mirroring ``_dig``: never raise, never render a nonsense count. Each case
# below also checks that ``days`` and ``winner`` SURVIVE the failure — the rules
# are independent, and withholding a standing is not a reason to throw away the
# two facts that were read successfully.

_WITHHELD_STANDINGS_CASES = [
    (_MALFORMED_ROSTER_GAME, "roster entries carry no parenthesised role"),
    (_UNKNOWN_ROLE_ROSTER_GAME, "roster entries carry roles this game never had"),
    (_UNATTRIBUTABLE_DEATH_GAME, "a death bullet has no readable role label"),
]


@pytest.mark.parametrize(
    ("text", "why"),
    _WITHHELD_STANDINGS_CASES,
    ids=["malformed-roster", "unknown-roles", "unattributable-death"],
)
def test_an_unreadable_shape_withholds_the_standings_without_raising(
    text: str, why: str
) -> None:
    """Both counts go to ``None`` together, and nothing else is lost."""
    summary = _summary(text)
    assert (summary.law_abiding, summary.mafia) == (None, None), (
        f"expected both standings withheld ({why}); got "
        f"{summary.law_abiding}-{summary.mafia}"
    )
    assert summary.days == 1, "the Days played are still readable and still reported"
    assert summary.winner == WINNER_MAFIA, (
        "the winner sentence is read by an independent rule and must survive a "
        "roster or death line this parser cannot read"
    )


def test_each_malformed_fixture_really_differs_from_the_game_it_was_built_from() -> None:
    """Precondition: ``str.replace`` actually replaced something.

    Three of the defensive fixtures are edits of ``_MODERN_MAFIA_WIN``. If a
    later change to that fixture's wording made a replacement a no-op, every
    "withholds the standings" assertion above would silently become an
    assertion about a well-formed game — and would fail for the right-looking
    wrong reason. This states the dependency instead.
    """
    for name, variant in (
        ("_MALFORMED_ROSTER_GAME", _MALFORMED_ROSTER_GAME),
        ("_UNKNOWN_ROLE_ROSTER_GAME", _UNKNOWN_ROLE_ROSTER_GAME),
        ("_UNATTRIBUTABLE_DEATH_GAME", _UNATTRIBUTABLE_DEATH_GAME),
    ):
        assert variant != _MODERN_MAFIA_WIN, (
            f"{name} is byte-identical to _MODERN_MAFIA_WIN — its str.replace "
            "no longer matches anything, so it tests nothing"
        )


def test_the_two_standings_never_travel_alone() -> None:
    """One real number beside an invented one is the outcome to avoid.

    Whatever makes one count unknowable makes the other unknowable too — the
    arithmetic shares a roster and a death list — so the pair is always both or
    neither, in every case this module exercises.
    """
    for name, text in (
        ("malformed roster", _MALFORMED_ROSTER_GAME),
        ("unknown roles", _UNKNOWN_ROLE_ROSTER_GAME),
        ("unattributable death", _UNATTRIBUTABLE_DEATH_GAME),
        ("negative arithmetic", _NEGATIVE_ARITHMETIC_GAME),
        ("no ending", _NO_ENDING_GAME),
        ("modern mafia win", _MODERN_MAFIA_WIN),
        ("pre-022 law win", _PRE_022_LAW_WIN),
        ("runaway", _RUNAWAY_GAME),
    ):
        summary = _summary(text)
        assert (summary.law_abiding is None) == (summary.mafia is None), (
            f"{name}: one standing is known and the other is not "
            f"({summary.law_abiding!r}, {summary.mafia!r}) — a row would show a "
            "real number beside an invented one"
        )


def test_more_deaths_than_players_withholds_rather_than_reporting_a_negative() -> None:
    """The guard that has never fired, and must not be removed for that reason.

    Three Law-abiding deaths against a roster of two is impossible from a
    well-formed transcript — 0 occurrences in the committed corpus, and the win
    rule held in every game that records an ending. It is a sign the text is not
    what this parser thinks it is, so the answer is silence, not ``-1``.
    """
    summary = _summary(_NEGATIVE_ARITHMETIC_GAME)
    assert (summary.law_abiding, summary.mafia) == (None, None), (
        f"derived {summary.law_abiding}-{summary.mafia}; a roster of 2 "
        "Law-abiding with 3 Law-abiding deaths must withhold, never render a "
        "negative count on the row"
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        _UNREADABLE_TEXT,
        _MODERN_MAFIA_WIN,
        _MALFORMED_ROSTER_GAME,
        _NEGATIVE_ARITHMETIC_GAME,
        _NO_ENDING_GAME,
        "<day>\n" * 3,
        "Full roster:",
        "• Night 1:",
    ],
    ids=[
        "empty",
        "unreadable",
        "well-formed",
        "malformed-roster",
        "negative",
        "no-ending",
        "bare-day-markers",
        "bare-roster-header",
        "bare-death-bullet",
    ],
)
def test_summarise_game_never_raises(text: str) -> None:
    """The contract in one line: pure, and it never raises.

    ``list_transcripts`` calls this once per file while building a panel, so an
    exception here is a traceback instead of a viewer — for one bad file among
    fifty good ones.
    """
    result = summarise_game(text)
    assert result is None or isinstance(result, GameSummary)


# ===========================================================================
# 5. The death-bullet discriminator (tech-spec §2.2, and the anti-vacuity pair)
# ===========================================================================
#
# The load-bearing detail of the whole derivation. The ``Who they really were:``
# persona reveal emits ``• Name (Role) — …`` bullets with NO cycle prefix, and
# the death bullets are ``• Night N: Name (Role)`` and
# ``• Day N: Name (Role) executed`` — both from one writer,
# ``graphia.nodes.endgame._format_kill_line``. The ``Night N:`` / ``Day N:``
# prefix is the ONLY discriminator between them.
#
# Dropping it does not crash and does not look broken: it reads a plausible pair
# of numbers on every row. So the two tests below are written as a pair — the
# precondition proves the prefix-free rule genuinely answers differently on THIS
# text, and the assertion pins the right answer. Removing the prefix requirement
# from ``eval_ledger._DEATH_BULLET_RE`` turns both red.

# What a prefix-free death rule would be: the production regex with the
# ``(?:Night|Day) N:`` requirement taken out, and nothing else changed.
_PREFIX_FREE_BULLET_RE = re.compile(r"(?m)^[ \t]*•[ \t]+.*$")

# How many players the persona-trap fixture's roster holds. Stated once here and
# checked against the fixture below, so the "reads every player as dead" claim
# cannot go stale if the roster is edited.
_TRAP_ROSTER_SIZE = 6


def test_the_persona_trap_fixture_is_shaped_like_the_writers_output() -> None:
    """Precondition: the trap is only a trap if it looks like a real game.

    It must carry the writer's kills header, its roster header and its persona
    header, or it stands for nothing the corpus actually contains. This is also
    the only place ``ENDGAME_HEADER_KILLS`` is used: the derivation deliberately
    does **not** key on it, so it is a fixture-shape check rather than a pin.
    """
    for header in (
        ENDGAME_HEADER_KILLS,
        ENDGAME_HEADER_ROSTER,
        ENDGAME_PERSONA_HEADER,
    ):
        assert header in _PERSONA_TRAP_GAME, (
            f"the trap fixture no longer carries {header!r}, so it is not "
            "shaped like anything the transcript writer emits"
        )


def test_a_prefix_free_bullet_rule_really_does_miscount_this_text() -> None:
    """Non-vacuity: the naive answer is genuinely wrong, on this input.

    Without this, "persona bullets are not counted as deaths" could pass against
    a fixture that has no persona bullets, or one whose persona bullets happen
    to cancel out. Here the prefix-free rule finds strictly more bullets than
    the real one, and the extra ones are exactly the four survivors — so its
    arithmetic is ``6 - 6`` on each side and it reports ``0-0``: not a crash,
    not a negative, a believable pair of numbers on every row of every run.
    """
    real_deaths = eval_ledger._DEATH_BULLET_RE.findall(_PERSONA_TRAP_GAME)
    all_bullets = _PREFIX_FREE_BULLET_RE.findall(_PERSONA_TRAP_GAME)
    assert len(all_bullets) > len(real_deaths), (
        f"the prefix-free rule found {len(all_bullets)} bullets and the real "
        f"rule {len(real_deaths)}. They agree on this fixture, so it cannot "
        "tell the two rules apart — either the fixture has lost its "
        f"`{ENDGAME_PERSONA_HEADER}` bullets, or the production rule has "
        "stopped requiring the `Night N:` / `Day N:` cycle prefix. Tech-spec "
        "045 §2.2: that prefix is the ONLY discriminator between a death and a "
        "persona reveal."
    )
    assert len(all_bullets) == _TRAP_ROSTER_SIZE, (
        f"the prefix-free rule found {len(all_bullets)} bullets against a "
        f"roster of {_TRAP_ROSTER_SIZE}; the fixture is built so that it reads "
        "every single player as dead and reports 0-0"
    )


def test_persona_reveal_bullets_are_not_counted_as_deaths() -> None:
    """The assertion the precondition above protects: this game is ``2-2``.

    Two players died and four survived; the four survivors each have a persona
    bullet. A rule that matched a bare ``• `` would read all six as dead and put
    ``0-0`` on the row — and on every other row, since every finished game in
    the corpus carries this section.
    """
    summary = _summary(_PERSONA_TRAP_GAME)
    assert (summary.law_abiding, summary.mafia) == (2, 2), (
        f"derived {summary.law_abiding}-{summary.mafia}, expected 2-2. If this "
        "reads 0-0, `eval_ledger._DEATH_BULLET_RE` has stopped requiring the "
        "`Night N:` / `Day N:` cycle prefix and is counting the "
        f"`{ENDGAME_PERSONA_HEADER}` bullets as deaths."
    )


def test_the_trap_fixtures_roster_really_is_the_size_the_claim_assumes() -> None:
    """Precondition for the ``0-0`` claim: roster size, read off the fixture."""
    roster = eval_ledger._parse_roster(_PERSONA_TRAP_GAME)
    assert roster is not None, "the trap fixture's roster line has become unreadable"
    assert sum(roster) == _TRAP_ROSTER_SIZE, (
        f"the trap fixture's roster holds {sum(roster)} players, not "
        f"{_TRAP_ROSTER_SIZE}; update _TRAP_ROSTER_SIZE or the fixture so the "
        "prefix-free rule still lands on exactly 0-0"
    )


def test_both_death_bullet_shapes_are_counted() -> None:
    """A Night kill and a Day execution are both deaths, and both are read.

    The trap fixture holds one of each — ``• Night 1: Avery (Law-abiding
    Citizen)`` and ``• Day 1: Leif (Law-abiding Citizen) executed`` — so a rule
    that recognised only one shape would read ``3-2`` rather than ``2-2``.
    """
    matched = eval_ledger._DEATH_BULLET_RE.findall(_PERSONA_TRAP_GAME)
    assert len(matched) == 2, f"expected both death bullets, matched {matched!r}"
    assert any("Night 1:" in line for line in matched), "the Night kill was missed"
    assert any("Day 1:" in line for line in matched), "the execution was missed"


# ===========================================================================
# 6. Writer pinning (tech-spec §2.2, "writer coupling")
# ===========================================================================
#
# ``eval_ledger`` holds its OWN copies of the writer's sentences and labels, so
# the model-free viewer never drags ``graphia.prompts``' model-facing prompt
# stack in. That independence buys a coupling nothing else in the suite would
# notice: reword a winner sentence in ``prompts.py`` and the derivation silently
# stops finding it — no crash, no failing test, just every row of every run
# reading as a game with no recorded ending, retroactively, for every game
# already preserved.
#
# So each literal is pinned to the constant that writes it, and each failure
# message says what breaks and where to fix it.

_CONSEQUENCE = (
    "The derivation matches the writer's text by substring. A rewording that "
    "is not mirrored into eval_ledger.py does not fail loudly anywhere else — "
    "it silently blanks this fact on EVERY row of EVERY run in the viewer, "
    "retroactively, for every game already preserved. Update the literal in "
    "eval_ledger.py's spec-045 section (and its `# Writer:` comment) to match."
)

_PINNED_LITERALS = [
    # (local name, local value, writer name, writer value)
    (
        "_WINNER_SENTENCE_LAW_ABIDING",
        eval_ledger._WINNER_SENTENCE_LAW_ABIDING,
        "graphia.prompts.ENDGAME_WINNER_LAW",
        ENDGAME_WINNER_LAW,
    ),
    (
        "_WINNER_SENTENCE_MAFIA",
        eval_ledger._WINNER_SENTENCE_MAFIA,
        "graphia.prompts.ENDGAME_WINNER_MAFIA",
        ENDGAME_WINNER_MAFIA,
    ),
    (
        "_WINNER_SENTENCE_RUNAWAY",
        eval_ledger._WINNER_SENTENCE_RUNAWAY,
        "graphia.prompts.ENDGAME_WINNER_RUNAWAY",
        ENDGAME_WINNER_RUNAWAY,
    ),
    (
        "_WINNER_SENTENCE_DRAW",
        eval_ledger._WINNER_SENTENCE_DRAW,
        "graphia.prompts.ENDGAME_WINNER_DRAW",
        ENDGAME_WINNER_DRAW,
    ),
    (
        "_ROSTER_HEADER",
        eval_ledger._ROSTER_HEADER,
        "graphia.prompts.ENDGAME_HEADER_ROSTER",
        ENDGAME_HEADER_ROSTER,
    ),
    (
        "_ROLE_LABEL_MAFIA",
        eval_ledger._ROLE_LABEL_MAFIA,
        "graphia.tools.eval_transcript._ROLE_LABELS['mafia']",
        _ROLE_LABELS["mafia"],
    ),
    (
        "_ROLE_LABEL_LAW_ABIDING",
        eval_ledger._ROLE_LABEL_LAW_ABIDING,
        "graphia.tools.eval_transcript._ROLE_LABELS['law_abiding']",
        _ROLE_LABELS["law_abiding"],
    ),
]


@pytest.mark.parametrize(
    ("local_name", "local_value", "writer_name", "writer_value"),
    _PINNED_LITERALS,
    ids=[local_name for local_name, _, _, _ in _PINNED_LITERALS],
)
def test_each_local_literal_is_still_what_the_writer_emits(
    local_name: str, local_value: str, writer_name: str, writer_value: str
) -> None:
    """The guard against silent rot — the whole reason this file has teeth.

    ``eval_ledger`` keeps its own literals on purpose (tech-spec §2.2, *writer
    coupling*); this test is the price of that choice, and it is meant to be the
    loudest failure in the module.
    """
    assert local_value == writer_value, (
        f"eval_ledger.{local_name} is\n    {local_value!r}\n"
        f"but {writer_name} now writes\n    {writer_value!r}\n\n{_CONSEQUENCE}"
    )


def test_the_two_role_labels_are_exactly_the_writers_two_labels() -> None:
    """Not just equal one by one — the SET matches, so a third role is caught.

    The per-literal test above would still pass if the game grew a third role
    (the Expanded Role Roster is a live roadmap item): ``Mafia`` and
    ``Law-abiding Citizen`` would each still match, while every roster line and
    every death bullet naming the new role became unparsable — which withholds
    the standings of every game that had one.
    """
    local_labels = {
        eval_ledger._ROLE_LABEL_MAFIA,
        eval_ledger._ROLE_LABEL_LAW_ABIDING,
    }
    assert local_labels == set(_ROLE_LABELS.values()), (
        f"eval_ledger knows the role labels {sorted(local_labels)} but "
        f"graphia.tools.eval_transcript._ROLE_LABELS now writes "
        f"{sorted(_ROLE_LABELS.values())}.\n\n{_CONSEQUENCE}"
    )


def test_the_two_side_ids_are_the_games_own_role_ids() -> None:
    """``winner`` speaks the game's vocabulary, not a display word.

    :data:`~graphia.eval_ledger.WINNER_MAFIA` / ``WINNER_LAW_ABIDING`` are the
    ids ``PlayerState.role`` and ``GameState.winner`` use, which is what lets
    the viewer branch on them without translating — and what keeps a colour name
    out of the pure layer.
    """
    assert {WINNER_MAFIA, WINNER_LAW_ABIDING} == set(_ROLE_LABELS), (
        f"the side ids are {sorted({WINNER_MAFIA, WINNER_LAW_ABIDING})} but the "
        f"game's role ids are {sorted(_ROLE_LABELS)}"
    )


def test_the_winner_match_table_covers_every_sentence_the_writer_can_emit() -> None:
    """All four endings are in the table, and the two side-less ones say so.

    The table is what makes the runaway and draw sentences genuinely *read*
    rather than merely documented — neither occurs anywhere in the corpus, so
    nothing else would witness one being dropped from it.
    """
    table = dict(eval_ledger._WINNER_SENTENCES)
    assert table == {
        ENDGAME_WINNER_LAW: WINNER_LAW_ABIDING,
        ENDGAME_WINNER_MAFIA: WINNER_MAFIA,
        ENDGAME_WINNER_RUNAWAY: None,
        ENDGAME_WINNER_DRAW: None,
    }, (
        "eval_ledger._WINNER_SENTENCES no longer pairs the writer's four "
        f"endgame sentences with their sides.\n\n{_CONSEQUENCE}"
    )


def test_the_derivation_is_exported_from_the_module() -> None:
    """The four public names are part of ``eval_ledger``'s contract.

    The panel branches on ``WINNER_MAFIA`` / ``WINNER_LAW_ABIDING`` rather than
    re-typing ``"mafia"`` / ``"law_abiding"`` at the call site, so both ids are
    as much a part of the contract as the class and the function.
    """
    for name in (
        "GameSummary",
        "WINNER_MAFIA",
        "WINNER_LAW_ABIDING",
        "summarise_game",
    ):
        assert name in eval_ledger.__all__, (
            f"{name} is missing from graphia.eval_ledger.__all__"
        )


def test_a_game_summary_is_a_frozen_slotted_value_object() -> None:
    """House style, and the reason a summary can be shared without copying.

    Matches :class:`~graphia.eval_ledger.TranscriptEntry` and ``TableModel``: an
    entry hands its summary to the row builder, which must not be able to edit
    the listing's own state by writing through it.
    """
    summary = _summary(_MODERN_MAFIA_WIN)
    with pytest.raises((AttributeError, TypeError)):
        summary.days = 99  # type: ignore[misc]
    assert not hasattr(summary, "__dict__"), (
        "GameSummary has a __dict__, so it was not declared with slots=True"
    )


# ===========================================================================
# 7. The four corpus oracles (tech-spec §4; Slice 1 task 1.4)
# ===========================================================================
#
# Everything above asks whether the derivation gets the right answer on text
# this file wrote. This section asks the harder question — whether the six
# rules survive the committed store: hundreds of real preserved games spanning
# three transcript eras, not one of them written by anyone thinking about this
# parser.
#
# **Three of the four are properties; the first is a genuine oracle**, and the
# distinction is what makes it the strongest test in this module. (b), (c) and
# (d) are statements that must hold of *any* game and are checkable without
# knowing the answer in advance. (a) is different: the eval harness tallied
# each run's winners at **measurement** time, from the live ``GameState``, with
# no transcript anywhere in the loop — ``blunder_eval._tally_outcomes`` over the
# winners the graph itself reported — and stamped that tally into the record.
# Summing this parser's per-game winners over a run and comparing them against
# that recorded tally checks the whole derivation end to end (day markers,
# roster line, death bullets, winner sentence, every era) against a number
# produced by a completely independent route. Measured 2026-09-15: every
# record↔directory pair agreed, 0 mismatches.
#
# **One item per run directory** — not one per game, and not one sweep over
# everything. A failure then names the directory that broke, which is the unit
# the corpus actually grows and shrinks in (spec 017 writes one dir per measured
# run; ``make clean-transcripts`` deletes whole ones), and the suite grows by
# about one item per eval run rather than by fifty.
#
# **Properties, never golden counts.** Not one assertion below names a corpus
# count. Every count-shaped assertion is relative — ``== len(files)``,
# ``== len(_RUN_DIRS)`` — following ``tests/test_transcript_highlight.py``'s
# convention. The arithmetic is unforgiving: the corpus gains a run directory on
# every measured eval run, so ``assert checked == 583`` is a test that goes red
# on a green tree the first time someone commits a transcript. A live backlog
# item already records ~70 lines across the repo that rotted exactly that way.
#
# **Read-only, absolutely.** Nothing here creates, moves, writes or deletes
# anything under ``evals/``. See this module's docstring for why that matters
# more than usual.
#
# **The `<recap>` cross-check that is NOT here, and must not be added.** The
# obvious fifth oracle — check roster-minus-deaths against the in-game
# ``<recap>`` standings the moderator posts, of which the corpus holds over a
# thousand — is deliberately absent, and this note exists so that a future
# reader does not helpfully add it. An era-blind version of it was written and
# run during technical design and reported **24 disagreements in 1,278**; all 24
# were the check's fault, for two independent reasons a reader of the
# transcripts would not guess. First, the June-2026 era posted its recap at the
# *end* of the Day round, **after** that Day's execution, while every later era
# posts it before — so the same sentence means two different things depending on
# which run wrote it. Second, the last recap of a game that ends on a Night kill
# reports the **pre-kill** counts, and most preserved games end exactly that way.
# A recap is a thing the moderator said during play, not a statement of final
# standings, and it is a safe oracle for neither.

# ---------------------------------------------------------------------------
# Corpus discovery — DUPLICATED from tests/test_transcript_search.py (which in
# turn copied it from tests/test_transcript_highlight.py)
# ---------------------------------------------------------------------------
#
# **Deliberately copied rather than imported**, for the reason spelled out in
# ``tests/test_transcript_search.py``: importing ``_TRANSCRIPTS`` from a sibling
# test module makes that module import at *this* module's collection time,
# couples the two files' collection order, and turns a failure in one into a
# mysterious error in the other. Test modules are leaves; they may share via
# ``conftest.py`` or via production code, never via each other. The block is
# ~20 lines and its drift risk is nil — if the corpus ever moves, all three
# files fail loudly on the same day.
#
# Resolved from THIS FILE, never from the CWD: ``pytest`` may be invoked from
# anywhere (repo root, ``tests/``, an IDE runner) and this sweep must always
# find the same corpus. ``tests/`` sits directly under the repo root, so
# ``parents[1]`` is that root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_ROOT = _REPO_ROOT / "evals" / "transcripts"


def _discover_transcripts() -> list[Path]:
    """Every committed transcript, discovered — deliberately never hard-coded.

    Matches ``*.txt`` at any depth under the corpus root: that is the artifact
    form spec 017 writes (``<run-id>/game-NN.txt``) and it keeps a stray
    ``.DS_Store`` — a live hazard on macOS — from failing the suite on an
    undecodable read.
    """
    if not _CORPUS_ROOT.is_dir():
        return []
    return sorted(path for path in _CORPUS_ROOT.rglob("*.txt") if path.is_file())


_TRANSCRIPTS = _discover_transcripts()

# The sweep's unit. One item per run directory, for the reason in this section's
# banner.
_RUN_DIRS = sorted({path.parent for path in _TRANSCRIPTS})

# A fresh clone can legitimately carry no transcripts: the dir is NOT gitignored
# but its contents are curated commit-or-delete, and a shallow or partial
# checkout may have none. Skipping keeps ``pytest -q`` green there instead of
# failing on absent input data.
#
# Gated **per test**, never at module level: every synthetic test above needs no
# corpus at all, and a module-level ``pytest.skip(allow_module_level=True)``
# would take all of them down with the sweep on a corpus-less checkout,
# reporting a green run that had not exercised the derivation once.
_NO_CORPUS_REASON = (
    f"no committed transcripts found under {_CORPUS_ROOT} — "
    "nothing to sweep in this checkout"
)

_requires_corpus = pytest.mark.skipif(not _TRANSCRIPTS, reason=_NO_CORPUS_REASON)

# The sweep's parameters: one per run dir, or a single explicitly-skipped
# placeholder when there is no corpus. The placeholder is deliberate — an empty
# parameter list is reported according to pytest's ``empty_parameter_set_mark``
# setting, which this repo does not pin, so the skip is spelled out here instead
# of inherited from configuration.
_RUN_DIR_PARAMS = [pytest.param(run_dir, id=run_dir.name) for run_dir in _RUN_DIRS] or [
    pytest.param(None, marks=pytest.mark.skip(reason=_NO_CORPUS_REASON), id="no-corpus")
]


def _rel(path: Path) -> str:
    """Path relative to the repo root, for readable failure messages."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:  # pragma: no cover - corpus always lives under the root
        return str(path)


def _games_in(run_dir: Path) -> list[Path]:
    """The discovered transcripts of one run directory, in game order.

    Derived from ``_TRANSCRIPTS`` rather than re-globbing, so every item sweeps
    exactly the set the discovery above found and the per-item coverage counters
    below mean what they say.
    """
    return [path for path in _TRANSCRIPTS if path.parent == run_dir]


# ---------------------------------------------------------------------------
# The ledger side of the pairing
# ---------------------------------------------------------------------------
#
# **Not every run dir has a record, and not every record has a run dir.** The
# two sets overlap; neither contains the other, and assuming otherwise is the
# easiest way to write an oracle that silently checks nothing:
#
# * A record names its run's directory by NAME under ``run.transcript_dir``, and
#   :func:`graphia.eval_ledger.transcript_dir_for` resolves that against the
#   ledger's sibling ``transcripts/`` dir. Records that predate spec 017 carry
#   no such field at all, and neither do the ``persona-bench`` records, which
#   measure persona generation in isolation and play no games.
# * Committed directories exist that no record names — a run whose record was
#   never written, or was written to a throwaway ledger. Measured 2026-09-15:
#   two of them.
#
# So the pairing is built once, here, and the two oracles that need a record
# **skip** the directories that have none rather than passing vacuously over
# them. A skip says "this directory has no ledger record"; a silent pass would
# say "the ledger agrees", which would be a lie.
_LEDGER_PATH = _REPO_ROOT / "evals" / "blunder-ledger.yaml"


# What :func:`_pair_records_with_run_dirs` hands back: the mapping, the
# doubly-claimed directory names, and whatever stopped the ledger loading.
type _Pairing = tuple[dict[Path, RawRecord], list[str], str | None]


def _pair_records_with_run_dirs() -> _Pairing:
    """Map each named transcript directory to the record that names it.

    Returns the mapping, the names of any directory claimed by **more than one**
    record, and a description of whatever stopped the ledger loading (``None``
    when it loaded cleanly).

    Defensive at module scope on purpose: a malformed ledger raises
    :class:`~graphia.eval_ledger.LedgerParseError`, and letting that escape here
    would be a *collection* error that takes down every synthetic test in this
    file along with the sweep. It is captured instead, and
    :func:`test_the_committed_ledger_really_does_pair_with_the_run_directories`
    turns it into one loud, readable failure.
    """
    try:
        records = load_ledger(_LEDGER_PATH)
    except (LedgerParseError, OSError) as exc:  # pragma: no cover - broken ledger
        return ({}, [], f"{type(exc).__name__}: {exc}")

    paired: dict[Path, RawRecord] = {}
    duplicates: list[str] = []
    for record in records:
        directory = transcript_dir_for(record, _LEDGER_PATH)
        if directory is None:
            continue
        if directory in paired:
            duplicates.append(directory.name)
            continue
        paired[directory] = record
    return (paired, duplicates, None)


(
    _RECORDS_BY_RUN_DIR,
    _DUPLICATE_CLAIMS,
    _LEDGER_LOAD_ERROR,
) = _pair_records_with_run_dirs()

# A sentinel distinct from ``None``, so "the key is absent" stays distinguishable
# from "the key is present and null" — the same distinction
# :data:`graphia.eval_ledger._MISSING` draws for the viewer's cells, reused here
# rather than reinvented.
_MISSING = eval_ledger._MISSING


def _recorded_count(record: RawRecord, dotted_key: str) -> int:
    """One ``outcomes`` count off a record, with an absent key read as ``0``.

    Mirrors :func:`graphia.eval_ledger._resolution_cell`'s reading exactly: a key
    absent from a **present** ``outcomes`` block is a real zero, because the
    harness only began writing it at some later spec. ``outcomes.runaway`` is the
    live case — spec 023 introduced it, and the two oldest paired records in the
    committed ledger predate it (measured 2026-09-15). A non-integer value reads
    as ``0`` too rather than raising; the tally comparison then fails loudly,
    which is the right outcome for a record that is not shaped like a record.
    """
    value = eval_ledger._dig(record, dotted_key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


# The moderator's own Day announcement, and the INDEPENDENT signal oracle (d)
# checks the derivation against. Deliberately not
# :data:`graphia.eval_ledger._DAY_MARKER_RE`: that rule counts the ``<day>``
# structural marker, this one reads the sentence a human sees inside the game,
# and an oracle that reused the production regex would only prove the regex
# equals itself.
#
# Leading blanks are tolerated because the pre-022 era indents every moderator
# line by two spaces. Unlike the winner sentence, there is **no**
# ``Moderator: ``-prefixed form of this line anywhere in the committed store
# (measured 2026-09-15) — that era simply indents it — so no speaker prefix is
# allowed for here. Writer: ``graphia.tools.eval_transcript``'s ``day_open``
# branch, ``f"Day {day_index} begins."``.
_DAY_ANNOUNCEMENT_RE = re.compile(r"(?m)^[ \t]*Day[ \t]+(\d+)[ \t]+begins\.")

# How many individual problems one failing item prints before it stops. A
# derivation that broke would otherwise print one line per game in the directory
# and bury the first — which is the one worth reading.
_MAX_REPORTED_PROBLEMS = 10


def _reported(problems: list[str]) -> list[str]:
    """The first few problems, plus an honest count of what is not shown."""
    shown = problems[:_MAX_REPORTED_PROBLEMS]
    if len(problems) > len(shown):
        shown = [*shown, f"… and {len(problems) - len(shown)} further problem(s)"]
    return shown


# ---------------------------------------------------------------------------
# The two preconditions
# ---------------------------------------------------------------------------
#
# Neither is an oracle. Each states something that has to be true before an
# oracle below means anything, and each exists because the corresponding oracle
# can go silently vacuous in a way that reports GREEN. Both were written after
# mutation-testing the four oracles against the committed store and finding
# exactly one that a real, plausible breakage walked straight past.


@_requires_corpus
def test_the_committed_ledger_really_does_pair_with_the_run_directories() -> None:
    """NON-VACUITY for oracles (a) and (c) — without this, every skip reads green.

    Both of those oracles skip a run directory that no record names, which is
    the honest report for the directories the store actually holds unpaired. But
    the same skip is what a *broken pairing* looks like: a renamed
    ``run.transcript_dir`` field, a moved ledger, a
    :func:`~graphia.eval_ledger.transcript_dir_for` that started returning
    ``None``. Every item would then skip, ``pytest -q`` would report a green run,
    and the strongest test in this module would have stopped running without
    anyone noticing.

    So: the pairing must be non-empty, and no directory may be claimed by two
    records. The second is ADR 014's invariant read from the outside — a run
    rewrites **its own** record while it is alive and never another run's — and
    a violation would mean this module was comparing a directory's games against
    somebody else's tally.
    """
    assert _LEDGER_LOAD_ERROR is None, (
        f"the committed ledger at {_rel(_LEDGER_PATH)} could not be read "
        f"({_LEDGER_LOAD_ERROR}), so no run directory can be paired with its "
        "record and both the ledger oracle and the lineup oracle are skipping "
        "every item"
    )

    if not _LEDGER_PATH.is_file():
        pytest.skip(
            f"no committed ledger at {_rel(_LEDGER_PATH)} — transcripts are "
            "present but there is nothing to pair them with in this checkout"
        )

    assert _RECORDS_BY_RUN_DIR, (
        f"{len(_TRANSCRIPTS)} transcripts in {len(_RUN_DIRS)} run directories "
        f"were discovered, and the ledger at {_rel(_LEDGER_PATH)} pairs with "
        "NONE of them. Either no record carries a `run.transcript_dir` any "
        "more, or `transcript_dir_for` no longer resolves it against the "
        "ledger's sibling `transcripts/` dir. Until this is fixed the ledger "
        "oracle and the lineup oracle skip every single item and report green."
    )

    assert not _DUPLICATE_CLAIMS, (
        "these transcript directories are claimed by more than one ledger "
        f"record: {sorted(set(_DUPLICATE_CLAIMS))}. ADR 014: a run rewrites its "
        "own record and never another run's, so a directory has at most one. "
        "Whichever record won the pairing, at least one of this module's "
        "oracles is comparing a run's games against a different run's tally."
    )


@_requires_corpus
def test_the_store_yields_standings_and_rosters_for_the_oracles_to_check() -> None:
    """NON-VACUITY for oracles (b) and (c) — and it is not a hypothetical.

    Both of those oracles skip a game they cannot read: (b) passes over a game
    whose standings are withheld, (c) over a game with no readable roster line.
    That is correct — a game with no recorded ending genuinely has neither — but
    it means an item that read **nothing at all** reports exactly like an item
    that read everything and found no problem. So the denominators are asserted
    here, once, over the whole store: at least one game must yield standings,
    and at least one game under a recorded lineup must yield a roster.

    **This was found by mutation, not by worrying.** Drop the ``Night N:`` /
    ``Day N:`` cycle prefix from ``eval_ledger._DEATH_BULLET_RE`` — the single
    load-bearing detail of the whole derivation, the one section 5 exists for —
    and run the four oracles over the committed store. Oracle (b) passes on
    **every** item. The reason is worth recording, because it is not the failure
    anyone would predict: the persona reveal names enough survivors that the
    persona bullets plus the real deaths exceed the roster, the arithmetic goes
    negative, and :func:`~graphia.eval_ledger._parse_survivors`'s guard withholds
    the standings of every finished game in the store. Oracle (b) then has
    nothing left to contradict and reports green over an empty set. (Measured
    2026-09-15: with the prefix dropped, every game that had standings lost
    them, and not one landed on the believable ``0-0`` the synthetic trap in
    section 5 produces — the real transcripts overshoot into the negative guard
    where the fixture stops exactly at zero.) This test is what turns that
    silent green into one loud failure.

    No count is asserted, only non-emptiness — the covered share moves with
    every eval run, per this section's banner.
    """
    with_standings = [
        path
        for path in _TRANSCRIPTS
        if (summary := summarise_game(read_transcript(path))) is not None
        and summary.law_abiding is not None
    ]
    assert with_standings, (
        f"not one of the {len(_TRANSCRIPTS)} committed games yields final "
        "standings, so the win-rule oracle is checking an empty set on every "
        "run directory and reporting green. The derivation is withholding "
        "everywhere — the likeliest cause is the death rule: if "
        "`_DEATH_BULLET_RE` has stopped requiring the `Night N:` / `Day N:` "
        "cycle prefix it counts every persona-reveal bullet as a death, the "
        "arithmetic goes negative, and `_parse_survivors` withholds every game "
        "in the store. See section 5."
    )

    # Oracle (c)'s own denominator, not the store's: it only ever looks at games
    # in a run directory whose record carries a lineup, so the non-vacuity claim
    # has to be made over exactly that set.
    under_a_lineup = [
        path
        for run_dir in _RUN_DIRS
        if (record := _RECORDS_BY_RUN_DIR.get(run_dir)) is not None
        and eval_ledger._dig(record, "settings.lineup", _MISSING) is not _MISSING
        for path in _games_in(run_dir)
    ]
    readable_rosters = [
        path
        for path in under_a_lineup
        if eval_ledger._parse_roster(read_transcript(path)) is not None
    ]
    assert readable_rosters, (
        f"{len(under_a_lineup)} committed game(s) sit in a run directory whose "
        "record carries a lineup, and not one of them yields a readable "
        "`Full roster:` line — so the lineup oracle is checking an empty set on "
        "every run directory and reporting green. Either the roster header has "
        "been reworded in `graphia.prompts.ENDGAME_HEADER_ROSTER` (section 6 "
        "pins that), or `_parse_roster` no longer recognises the role labels "
        "the entries are parenthesised with."
    )


# ---------------------------------------------------------------------------
# Oracle (a) — the ledger as oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_the_derived_winners_reproduce_the_runs_recorded_tally(run_dir: Path) -> None:
    """The strongest test in this module: parser vs. the harness's own numbers.

    Sum this parser's per-game winners over one run directory and the three
    buckets must equal what the run's record says —
    ``outcomes.law_abiding.wins``, ``outcomes.mafia.wins``, and
    ``runaway + draw + no_winner`` for the games that ended with no winning side
    — with the number of preserved games equal to ``outcomes.games``.

    **Why that is worth more than the three properties below.** Those check the
    derivation for self-consistency. This checks it against a number nothing in
    this file produced: ``blunder_eval._tally_outcomes`` counted those winners at
    measurement time, from the live ``GameState`` the graph reported, with no
    transcript in the loop and a great deal of transcript-format churn since.
    Agreement means the winner sentence is being read correctly in **every** era
    the run directories span — including the pre-022 one, where the sentence
    hides behind a ``  Moderator: `` prefix and a line-anchored match would
    silently lose ten finished games. Section 1's era pair is the unit witness
    for that; this is the item that would notice it happening across the whole
    store.

    A directory that no record names is **skipped**, not passed: the ledger
    cannot be an oracle for a run it never recorded, and a silent pass would
    claim an agreement that was never checked.
    """
    record = _RECORDS_BY_RUN_DIR.get(run_dir)
    if record is None:
        pytest.skip(
            f"{run_dir.name} is a committed transcript directory that no ledger "
            "record names — there is no recorded tally to check it against"
        )
    if eval_ledger._dig(record, "outcomes", _MISSING) is _MISSING:
        pytest.skip(
            f"{run_dir.name}'s ledger record carries no `outcomes` block (it "
            "predates spec 013), so there is no recorded tally to check"
        )

    files = _games_in(run_dir)
    assert files, f"no transcripts discovered under {run_dir} — the item is vacuous"

    derived: dict[str | None, int] = {}
    unsummarisable: list[str] = []
    for path in files:
        summary = summarise_game(read_transcript(path))
        if summary is None:
            # No `<day>` marker at all. The row shows this game's number alone
            # and highlights no side, so it belongs in the no-winning-side
            # bucket — but it is a different *kind* of no-winner from a runaway,
            # and the failure message says so rather than leaving a reader to
            # work it out.
            unsummarisable.append(_rel(path))
        winner = summary.winner if summary is not None else None
        derived[winner] = derived.get(winner, 0) + 1

    law_abiding_wins = derived.get(WINNER_LAW_ABIDING, 0)
    mafia_wins = derived.get(WINNER_MAFIA, 0)
    no_side = derived.get(None, 0)

    # The coverage counter, and a second job besides: every game must have
    # landed in one of the three buckets the ledger knows about, so a `winner`
    # that is neither side nor `None` is caught here rather than vanishing from
    # the comparison below.
    assert law_abiding_wins + mafia_wins + no_side == len(files), (
        f"{run_dir.name}: {len(files)} transcript(s) produced winner values "
        f"{sorted(derived, key=str)!r} — something outside "
        "{WINNER_LAW_ABIDING, WINNER_MAFIA, None} is being derived, and the "
        "tally below would silently drop it"
    )

    derived_tally = (law_abiding_wins, mafia_wins, no_side, len(files))
    recorded_tally = (
        _recorded_count(record, "outcomes.law_abiding.wins"),
        _recorded_count(record, "outcomes.mafia.wins"),
        _recorded_count(record, "outcomes.runaway")
        + _recorded_count(record, "outcomes.draw")
        + _recorded_count(record, "outcomes.no_winner"),
        _recorded_count(record, "outcomes.games"),
    )

    def _shape(tally: tuple[int, int, int, int]) -> str:
        return (
            f"law_abiding={tally[0]} mafia={tally[1]} no-side={tally[2]} "
            f"games={tally[3]}"
        )

    detail = ""
    if unsummarisable:
        detail = (
            f"\n  NOTE: {len(unsummarisable)} of these games yielded no summary "
            "at all (no `<day>` marker found) and were counted as no-side: "
            f"{', '.join(_reported(unsummarisable))}"
        )
    assert derived_tally == recorded_tally, (
        f"{run_dir.name}: the winners derived from the preserved games do not "
        "reproduce the tally this run recorded at measurement time.\n"
        f"  derived from {len(files)} transcript(s): {_shape(derived_tally)}\n"
        f"  recorded in the ledger:                  {_shape(recorded_tally)}"
        f"{detail}\n"
        "The recorded side is authoritative — `blunder_eval._tally_outcomes` "
        "counted it from the live GameState with no transcript in the loop. A "
        "disagreement is therefore the derivation's, and the likeliest cause is "
        "the winner sentence: a rewording in `graphia.prompts`, or a match that "
        "has become line-anchored and so cannot see the pre-022 era's indented "
        "`  Moderator: ` form."
    )


# ---------------------------------------------------------------------------
# Oracle (b) — the win rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_every_derived_standing_obeys_the_games_own_win_rule(run_dir: Path) -> None:
    """The standings and the winner must be able to be true at the same time.

    Three properties, over every game in one run directory whose standings the
    derivation reports:

    * **No count is negative.** Guarded in
      :func:`~graphia.eval_ledger._parse_survivors`, which withholds rather than
      rendering one; this is the corpus-wide statement that it never has to.
    * **A Law-abiding win implies no Mafioso alive.** That is how the town wins
      — there is no other route.
    * **A Mafia win implies ``mafia > 0`` and ``mafia >= law_abiding``.** Parity
      is the Mafia win condition, and a side that is extinct has not won.

    This catches an arithmetic error the ledger oracle would not: a derivation
    that read the winner perfectly and the roster or the death bullets wrongly
    reproduces every recorded tally and still puts nonsense standings on every
    row. The death-bullet trap of section 5 is exactly that failure, and it
    would surface here as Mafia wins recorded at ``0-0``.

    A game whose standings are withheld (no recorded ending, an unreadable
    roster) is counted and skipped, never treated as ``0-0``; the coverage
    counter proves the item looked at every file it was given.
    """
    files = _games_in(run_dir)
    assert files, f"no transcripts discovered under {run_dir} — the item is vacuous"

    problems: list[str] = []
    checked = 0
    withheld = 0
    for path in files:
        summary = summarise_game(read_transcript(path))
        if summary is None or summary.law_abiding is None or summary.mafia is None:
            withheld += 1
            continue
        checked += 1
        law_abiding, mafia, winner = summary.law_abiding, summary.mafia, summary.winner
        standings = f"{law_abiding}-{mafia}"
        if law_abiding < 0 or mafia < 0:
            problems.append(
                f"{_rel(path)}: negative standing {standings} — "
                "`_parse_survivors` must withhold rather than render one"
            )
        if winner == WINNER_LAW_ABIDING and mafia != 0:
            problems.append(
                f"{_rel(path)}: a Law-abiding win at {standings}, but the town "
                "wins only by leaving no Mafioso alive"
            )
        if winner == WINNER_MAFIA and not (mafia > 0 and mafia >= law_abiding):
            problems.append(
                f"{_rel(path)}: a Mafia win at {standings}, but the Mafia win "
                "only on parity or better, and an extinct side has not won"
            )

    assert checked + withheld == len(files), (
        f"{run_dir.name}: examined {checked + withheld} of {len(files)} "
        "discovered transcripts — the sweep has silently narrowed"
    )
    assert not problems, "\n  ".join(
        [
            f"{run_dir.name}: {len(problems)} of {checked} derived standing(s) "
            "cannot be true beside the winner derived from the same game:",
            *_reported(problems),
        ]
    )


# ---------------------------------------------------------------------------
# Oracle (c) — the recorded lineup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_every_derived_roster_is_the_size_the_run_was_configured_to_play(
    run_dir: Path,
) -> None:
    """The roster the parser reads is the table the run was set up to deal.

    ``settings.lineup.num_citizens + num_mafia`` is what the harness was
    *configured* with, written into the record before a card was dealt; the
    roster line is what the finished game says it dealt. They must agree, and
    the check matters because the roster is the base the whole survivor
    arithmetic stands on — a roster read one player short makes every standing
    in that run wrong by one, silently and plausibly.

    **This oracle deliberately does not cover every committed game, and that is
    a shortfall in the inputs rather than a failure.** Three gaps, each handled
    on its own terms:

    * A run directory that **no record names** has no configured lineup to
      compare against — the item skips.
    * A record that **predates the ``settings.lineup`` field** (spec 014 added
      it; older records carry no lineup at all) — the item skips.
    * A game that **records no ending** carries no ``Full roster:`` line, so
      there is nothing to size — that game is counted as withheld and the sweep
      carries on.

    No coverage fraction is asserted, for the reason in this section's banner:
    the covered share moves with every eval run and with every old record, so
    pinning it would be pinning the corpus.
    """
    record = _RECORDS_BY_RUN_DIR.get(run_dir)
    if record is None:
        pytest.skip(
            f"{run_dir.name} is a committed transcript directory that no ledger "
            "record names — there is no configured lineup to check it against"
        )
    if eval_ledger._dig(record, "settings.lineup", _MISSING) is _MISSING:
        pytest.skip(
            f"{run_dir.name}'s ledger record carries no `settings.lineup` (it "
            "predates spec 014), so the run's table size was never recorded"
        )

    citizens = eval_ledger._dig(record, "settings.lineup.num_citizens")
    mafiosos = eval_ledger._dig(record, "settings.lineup.num_mafia")
    if not isinstance(citizens, int) or not isinstance(mafiosos, int):
        pytest.skip(
            f"{run_dir.name}'s recorded lineup is not a pair of integers "
            f"({citizens!r}, {mafiosos!r}) — nothing to compare a roster against"
        )
    seats = citizens + mafiosos

    files = _games_in(run_dir)
    assert files, f"no transcripts discovered under {run_dir} — the item is vacuous"

    problems: list[str] = []
    checked = 0
    withheld = 0
    for path in files:
        roster = eval_ledger._parse_roster(read_transcript(path))
        if roster is None:
            withheld += 1
            continue
        checked += 1
        if sum(roster) != seats:
            problems.append(
                f"{_rel(path)}: the roster line names {sum(roster)} player(s) "
                f"({roster[0]} Law-abiding, {roster[1]} Mafia) but the run was "
                f"configured for {seats} ({citizens} + {mafiosos})"
            )

    assert checked + withheld == len(files), (
        f"{run_dir.name}: examined {checked + withheld} of {len(files)} "
        "discovered transcripts — the sweep has silently narrowed"
    )
    assert not problems, "\n  ".join(
        [
            f"{run_dir.name}: {len(problems)} of {checked} readable roster(s) "
            "are not the size this run was configured to play. The roster is "
            "the base of every survivor count in this run, so a mis-read one "
            "puts a wrong pair of numbers on every row:",
            *_reported(problems),
        ]
    )


# ---------------------------------------------------------------------------
# Oracle (d) — day-count agreement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_dir", _RUN_DIR_PARAMS)
def test_the_day_count_agrees_with_the_moderators_own_announcements(
    run_dir: Path,
) -> None:
    """Two independent readings of "how long did this game run" must agree.

    The derivation counts the structural ``<day>`` marker. This checks that
    count against the highest ``Day N begins.`` the moderator announced inside
    the game — a different signal, emitted by a different part of the writer,
    and the one functional-spec §2.1 actually promises a reviewer: *the number a
    reviewer sees is a Day the moderator announced out loud, so it is a number
    they can go and find*. This item is what makes that promise checkable.

    Both directions are reported, because they fail for different reasons: a
    game with ``<day>`` markers and no announcement means the announcement's
    wording moved, and a game that announces a Day with no ``<day>`` marker
    means the structural marker did.
    """
    files = _games_in(run_dir)
    assert files, f"no transcripts discovered under {run_dir} — the item is vacuous"

    problems: list[str] = []
    examined = 0
    for path in files:
        examined += 1
        text = read_transcript(path)
        summary = summarise_game(text)
        announced = [int(number) for number in _DAY_ANNOUNCEMENT_RE.findall(text)]
        if summary is None and not announced:
            # Neither signal is present: an empty, truncated or unreadable file.
            # There is nothing for the two readings to disagree about.
            continue
        if not announced:
            problems.append(
                f"{_rel(path)}: the derivation counted {summary.days} Day(s) "
                "but the text announces none — `Day N begins.` has been "
                "reworded, or this game's text is not what it appears to be"
            )
            continue
        if summary is None:
            problems.append(
                f"{_rel(path)}: the text announces Day {max(announced)} but no "
                "`<day>` marker was found — the structural marker has moved, "
                "and every game written that way now shows no summary at all"
            )
            continue
        if summary.days != max(announced):
            problems.append(
                f"{_rel(path)}: the derivation counted {summary.days} Day(s) "
                f"but the moderator announced up to Day {max(announced)}"
            )

    assert examined == len(files), (
        f"{run_dir.name}: examined {examined} of {len(files)} discovered "
        "transcripts — the sweep has silently narrowed"
    )
    assert not problems, "\n  ".join(
        [
            f"{run_dir.name}: {len(problems)} of {len(files)} game(s) disagree "
            "about how many Days they played:",
            *_reported(problems),
        ]
    )
