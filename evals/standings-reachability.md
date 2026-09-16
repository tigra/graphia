# Which final standings a Graphia game can reach — and why 3–0 almost never happens

*Analysis recorded 2026-09-16. Corpus: the 583 transcripts under `evals/transcripts/`, of which 563
carry a recorded ending (20 are unfinished games from interrupted runs and are excluded throughout).*

## The question

Across the three most recent 50-game evals — all `2026-09-14`, all the **6+2** lineup, 150 games total —
the outcomes include 4–0 and 2–0 Law-abiding wins and 2–2 Mafia wins, but **not one 3–0 Law-abiding win**.
Chance, or something about the game?

## The answer in one line

**3–0 is the only 6+2 town win that requires an idle Day — and an idle Day inside a winning game is the
rare event.** The structural fact is the *requirement*, not the absence. 3–0 itself is perfectly reachable
and does occur (4 times in 299 games); what is scarce is the ingredient it uniquely depends on.

Two separate claims, and it is worth keeping them apart:

- **Proved from the rules:** 3–0 in 6+2 happens if and only if the town makes zero mis-executions *and*
  wastes exactly one Day. There is no second route — in particular, a mis-execution **cannot** substitute
  for an idle Day.
- **Measured from the corpus:** idle Days are common in general (66% of 6+2 games have one) but rare in
  games the town wins (17%) — because, as derived below, a town win can only afford so many.

The absence in the recent runs is those two facts multiplied together. It is not itself structural.

## The model

Two mechanics do all the work, both verified against the corpus rather than assumed:

1. **Every Night kills a Citizen, never a Mafioso.** Reconstructing every final standing as
   `citizens_left = C − nights − e_L` reproduces the recorded ending in all 563 games. If a Night had ever
   taken a Mafioso the arithmetic would have broken.
2. **At most one execution per Day**, and a Night precedes every Day.

Notation, for a lineup of `C` Citizens and 2 Mafiosos:

| symbol | meaning |
|---|---|
| `d`   | the Day the second Mafioso is executed (so `d` Nights have also elapsed) |
| `e_L` | Citizens executed by mistake |
| `idle`| Days that passed with no execution at all, `= d − (2 + e_L)` |

## Why a winning town cannot afford idle Days

This is the part that explains everything else.

The Mafia win the moment `mafia ≥ citizens`. With 2 Mafiosos that floor is 2 Citizens, so in 6+2 the town's
**entire loss budget is 4 Citizens**. Every Day costs exactly one of them, because a Night precedes every
Day and every Night takes a Citizen. A Day therefore always spends one unit of budget, and what the town
gets back depends on how it spends the Day:

| the town's Day | budget spent | progress bought |
|---|---|---|
| executes a Mafioso | 1 | one of the two kills it needs |
| executes a Citizen (mis-execution) | **2** (Night kill **+** the victim) | none |
| idles (no execution) | 1 | **none** |

A town win needs `2 + e_L` productive Days plus `idle` wasted ones, and the whole sequence must fit inside
the 4-Citizen budget:

```
(2 + e_L) + idle  ≤  4        =>        idle  ≤  2 − e_L
```

**So a winning town may waste at most two Days, and only if it never mis-executes at all.** One
mis-execution and its idle allowance drops to zero. Three idle Days is an automatic loss. Meanwhile a
*losing* town faces no such ceiling — and unsurprisingly `idle = 3` is the single most common pattern in
the whole 6+2 corpus (132 of 299 games).

That ceiling is the mechanism behind the headline measurement:

| | P(at least one idle Day) |
|---|---|
| any 6+2 game | 66% (198/299) |
| Mafia win | 82% (186/228) |
| **town win** | **17% (12/71)** |

Idle Days are not rare. Idle Days *in winning games* are rare, because idling is one of the things that
turns a winning game into a losing one.

## Complete reachability — derived, then observed

### 6+2 (299 games, 71 town wins)

Combining the budget ceiling `idle ≤ 2 − e_L` with `idle ≥ 0` admits exactly four cells:

| standing | `e_L` | `d` | idle Days | route | observed |
|---|---|---|---|---|---|
| **4–0** | 0 | 2 | 0 | perfect and fast | 29 |
| **3–0** | 0 | 3 | **1** | perfect but **one wasted Day** | **4** |
| **2–0** | 0 | 4 | 2 | perfect but two wasted Days | 8 |
| **2–0** | 1 | 3 | 0 | fast but **one mis-execution** | 30 |

All 71 town wins land in these four cells and nowhere else.

Read the `idle` column downward and the asymmetry jumps out: 4–0 and the common 2–0 both need **zero**
idle Days, and 2–0 is additionally reachable *with* two. Only 3–0 pins the count to exactly one. It is the
sole standing that cannot be reached by a town that never wastes a Day.

The blocked variant is worth spelling out:

> **3–0 by mis-execution** would need `e_L = 1`, which forces `idle ≤ 1` from the ceiling and
> `d = 2` from `d + e_L = 3` — but three executions cannot fit in two Days. The cell is empty by
> arithmetic, not by luck. Corpus: **0 of 4** three-oh games contain a wrongful execution, against
> **30 of 38** two-oh games.

A mis-execution is not a slower path to the same place. It costs *two* Citizens against the same 4-Citizen
budget, so being wrong always overshoots 3–0 straight down to 2–0.

### 5+2 (264 games, 31 town wins)

The same budget with `C = 5` leaves only 3 Citizens to spend, so `(2 + e_L) + idle ≤ 3`:

| standing | `e_L` | `d` | idle Days | observed |
|---|---|---|---|---|
| **3–0** | 0 | 2 | 0 | 5 |
| **2–0** | 0 | 3 | 1 | 26 |

Here `e_L = 1` is infeasible for *any* town win, and all 31 observed town wins have `e_L = 0`, with zero
exceptions. 4–0 is arithmetically impossible.

Note that **3–0 means opposite things in the two lineups**: in 5+2 it is the flawless *fast* sweep with no
idle Day at all, in 6+2 it is the awkward middle that demands one. Pooling the lineups destroys the signal,
which is why everything above is split.

## The pool that matters: clean-sweep town wins

Since 3–0 requires `e_L = 0`, the only games that could ever have been 3–0 are clean-sweep town wins.
Conditioning on that pool (6+2, n = 41):

| idle Days | standing | count | share |
|---|---|---|---|
| 0 | 4–0 | 29 | 71% |
| **1** | **3–0** | **4** | **10%** |
| 2 | 2–0 | 8 | 20% |

So even among towns that played *flawlessly*, seven in ten wasted no Day at all and went straight to 4–0.

**An oddity worth flagging:** exactly-one idle Day (10%) is *rarer* than exactly-two (20%). The distribution
is non-monotone, which hints that stalls bunch — a town that fails to reach consensus once tends to fail
again — so "stall once, then recover and never stall again" is a narrower behavioural window than either
never stalling or stalling repeatedly. The exact idle-Day patterns among all 71 town wins are consistent
with that reading:

```
()      59      no idle Day
(1,)     4      Day 1 only          <- every 3-0 in the corpus
(1,2)    5
(1,3)    2
(2,3)    1
```

With only 12 town wins in the tail this is suggestive, not established; it is the one claim here that would
need more data. (Note the last Day can never be idle — the game ends on the execution that completes the
sweep — so for a 3-Day win the idle Day must be Day 1 or Day 2, and all four observed are Day 1.)

## What this means for the three 50-game runs

The town-win/idle-Day coupling holds for every model, though the base rates differ a lot (6+2 games):

| model | n | idle Day in town wins | idle Day in Mafia wins |
|---|---|---|---|
| `amazon.nova-pro-v1:0` | 112 | 62% (5/8) | 95% (99/104) |
| `qwen3-coder:30b` | 125 | 16% (5/31) | 83% (78/94) |
| `us.anthropic.claude-haiku-4-5` | 62 | **6% (2/32)** | 30% (9/30) |

Haiku is the extreme: it commits to an execution almost every Day, and in games it wins it idles in 2 of 32.
Its town wins split cleanly along the two routes the structure allows:

```
haiku, 6+2, 62 games -> 32 town wins
  4-0   perfect + fast                 14
  2-0   one mis-execution              16
  3-0   perfect + one idle Day          1
  2-0   perfect + two idle Days         1
```

Haiku is **accurate but never slow**. Playing cleanly it also plays fast, which lands on 4–0; erring it
mis-executes, which lands on 2–0. Of its 16 clean-sweep town wins only **1** wasted exactly one Day.

In the 50-game run of 2026-09-14 haiku produced **10 clean sweeps, every one with zero idle Days** — all 10
became 4–0. Taking `P(exactly one idle Day | clean sweep) ≈ 1/16 ≈ 0.06` from its full history:

```
P(no 3-0 across 10 clean sweeps) = (1 − 0.06)^10 ≈ 0.52
```

**Observing zero 3–0 wins was the most likely single result.** There is no anomaly left to explain.

## Structural or probabilistic? The honest split

Three different things get called "structural"; only the first two are proofs.

1. **Structurally impossible** — no legal sequence reaches it. A 4–0 in 5+2; any town win with ≤ 1 Citizen
   surviving; **a 3–0 containing a mis-execution**; a town win with 3 idle Days. Closed by arithmetic,
   never to appear however many games are run.
2. **Structurally single-ingredient** — reachable, but only through one specific behaviour. **3–0 in 6+2
   requires exactly one idle Day.** No substitute exists.
3. **Behaviourally scarce** — that one ingredient is something towns rarely do *while winning* (17%
   corpus-wide, 6% for haiku), itself a consequence of the budget ceiling in (1).

The absence of 3–0 in the recent runs is **(2) multiplied by (3)** — a structural requirement pointed at a
behaviour that is scarce for structural reasons of its own. The residual zero is a sampling outcome, but of
a probability already driven to roughly 6% per clean sweep, so the zero is the expected reading rather than
something needing an account.

**The practical consequence: 3–0 is a poor signal to track in 6+2.** It measures a narrow conjunction —
flawless deduction *plus* exactly one wasted Day — rather than anything about play quality on its own. The
two quantities actually worth reading are the **clean-sweep rate** (`e_L = 0` among town wins) and the
**idle-Day rate within wins**; the standings are just their product.

## Reproducing this

The derivation is pure arithmetic over the `Events this game:` block that every transcript carries — a list
of `• Night N: <name> (<role>)` and `• Day N: <name> (<role>) executed` lines. No model access and no ledger
read is required; the transcripts alone suffice.

```python
import re, pathlib, collections

ROOT = pathlib.Path("evals/transcripts")
rows = []
for f in sorted(ROOT.glob("*/game-*.txt")):
    t = f.read_text(errors="replace")
    roster = re.search(r"^Full roster: (.+)$", t, re.M)
    block  = re.search(r"^Events this game:\n((?:•.*\n)*)", t, re.M)
    if not (roster and block and "<endgame>" in t):
        continue                       # unfinished game
    C = len(re.findall(r"\(Law-abiding Citizen\)", roster.group(1)))
    M = len(re.findall(r"\(Mafia\)", roster.group(1)))
    events = [l for l in block.group(1).splitlines() if l.strip()]
    nights = [l for l in events if l.startswith("• Night")]
    execs  = [re.match(r"• Day (\d+): .*?\((.+?)\) executed", l).groups()
              for l in events if l.startswith("• Day")]
    e_L = sum(1 for _, role in execs if role != "Mafia")
    e_M = len(execs) - e_L
    # a game ending at Night N never played Day N, so don't count it as idle
    days = len(nights) - 1 if events[-1].startswith("• Night") else len(nights)
    exec_days = {int(d) for d, _ in execs}
    idle = [d for d in range(1, days + 1) if d not in exec_days]
    town = "Mafia have won" not in t.split("<endgame>\n")[1].split("\n")[0]
    rows.append(dict(lineup=f"{C}+{M}", town=town, e_L=e_L, idle=idle,
                     standing=f"{C - len(nights) - e_L}-{M - e_M}",
                     model=re.search(r"large_model=(\S+)", t).group(1)))

six = [r for r in rows if r["lineup"] == "6+2"]
tw  = [r for r in six if r["town"]]
for k, v in sorted(collections.Counter(
        (r["standing"], r["e_L"], len(r["idle"])) for r in tw).items()):
    print(k, v)
print("idle in town wins :", sum(1 for r in tw if r["idle"]), "/", len(tw))
print("idle in mafia wins:", sum(1 for r in six if not r["town"] and r["idle"]),
      "/", sum(1 for r in six if not r["town"]))
```

**One trap worth flagging.** A first pass derived the Day count as `max(night index, execution day)`, which
counts a phantom final Day whenever a game ends at Night N before Day N is played. That inflates the idle
count for Mafia wins by one and produced a striking but false finding ("one idle Day never occurs" — it
occurs 43 times). Town wins are unaffected, since they always end on a Day execution, so the reachability
tables survived the correction unchanged; the Mafia-side numbers did not. The guard in the snippet above is
the fix.
