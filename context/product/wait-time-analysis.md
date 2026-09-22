# Where Graphia Freezes

- **Status:** Analysis, 2026-09-22
- **Scope:** measurement only — no code changes. Remedies are routed to [`backlog.md`](backlog.md).
- **Evidence:** `.graphia/graphia.log` — 13,354 lines, 2026-04-22 → 2026-09-21, 480 measured waits
  across 207 sessions; and **40 AgentCore invocation traces** (2,636 spans) from the four remote
  sessions of 2026-08-30, 09-16 and 09-21.

---

## 1. The five moments a player waits

A player never sees a node name. They see a game that stops answering. These are the five points
in a game of Graphia where that happens, in the order you meet them.

Every number is measured from the moment the player **submitted** to the moment they are **asked
for something again**. Thinking time is excluded by construction. In remote mode the boundary is
exact: one invocation of the deployed Runtime *is* one wait, so each of the 40 traces times one of
these moments end to end.

Each moment below names **what the game is actually doing** and **what the player could be shown
instead**. The remedies are proposals for the backlog, not decisions.

### ① You type your name, and the game doesn't start

| | local (September) | remote |
| --- | --- | --- |
| typical | **16.3 s** | **43.8 s** |
| worst seen | 72.0 s | 51.9 s |

**What the game is doing.** It invents the other seven players from nothing. One call names the
whole roster; the roles are then dealt without the model; and then **each of the seven AI players
is given a character, one at a time**, every one of them shown the characters already written so
the next can be made deliberately unlike them. That accumulation is the feature (specs 031/034 —
personas are steered apart on purpose), and it is also why these seven calls cannot simply be run
at once without changing what they produce.

**On screen today.** Nothing. This is the player's first impression of the product, and remotely
it is the longest wait in the game.

**Proposed remedy — seat the table in front of the player.** The roster is public information the
moment it exists, so nothing needs to be withheld: print each player as they are invented —
*"Marco takes a seat."*, *"Lucia takes a seat."* — and the blank 44 s becomes seven visible beats
of the game assembling itself. Real progress, not a simulated bar.

### ② You say your piece, and the room keeps talking

| | local (September) | remote |
| --- | --- | --- |
| typical | **15.9 s** | **14.6 s** |
| 9 in 10 under | 61.0 s | 23.1 s |
| worst seen | 74.2 s | 38.9 s |

**What the game is doing.** Two different things, which is why this moment reads as two. First the
other players speak in turn, one model call each, and each speech is printed as it lands — about
1.7 s apart. Then, once the round is complete, **every surviving AI player is asked privately what
they now make of it** — seven more calls, one after another — and by the privacy rule none of what
they write may ever be shown.

**On screen today.** Progress, then a dead stop of **10 to 42 seconds** in the middle of a
conversation, with no explanation. This is the freeze most likely to be read as a crash.

**Proposed remedy — tell the player the table has gone quiet, and count it.** A line on the log's
border while the round settles: *"The table falls quiet — everyone is thinking it over."* The game
knows how many players have finished, so it can honestly say *4 of 7* without disclosing a word of
what any of them thought. The border rather than a row, for the reason spec 040 records in §7.

### ③ You speak last, and the whole night passes

| | local (September) | remote |
| --- | --- | --- |
| typical | 2.5 s | **44.0 s** |
| 9 in 10 under | **81.2 s** | — |
| worst seen | **126.7 s** | 44.0 s |

**What the game is doing.** Everything the night consists of, back to back: the round settles as
above; **each surviving AI writes its diary entry for the day** (seven more calls); the Day closes;
**the mafia confer privately and choose someone** (a call per mafioso); the kill resolves. Then, on
Night 2 onward, every surviving AI **reads its own diary back** — seven round-trips that remotely
run Gateway → Lambda → Memory. Those reads feed nothing to the model; they are kept deliberately as
the only thing that exercises the remote read path in a live game, so a broken one would not go
unnoticed. The player is, in effect, waiting on a health check.

**On screen today.** Almost nothing, for up to two minutes. The great majority is invisible by
design — diaries are private to their author and mafia talk is addressed to the mafia.

**Proposed remedy — narrate the night, because the game already has a Moderator who can.** The
night is already several distinct steps, so the Moderator can speak at each boundary it crosses:
*"Night falls."* → *"The town writes the day down."* → *"The Mafia are awake."* → *"The Mafia have
chosen."* Four beats across what is today the longest silence in the game, and they are atmosphere
rather than apology — this is the one freeze that could become a feature.

### ④ You vote

Effectively instant — a median under 4 seconds, often under one.

**What the game is doing.** Almost nothing: the AI ballots were gathered in the step that produced
the prompt, so by the time the player votes the count is nearly complete.

**Proposed remedy — none.** This moment is fine and should be left alone.

### ⑤ You cast the last vote, and the game is over — except it isn't

| | local (September) | remote |
| --- | --- | --- |
| typical | **10.5 s** | **63.9 s** |
| worst seen | 80.7 s (205.4 s all-time) | 77.9 s |

**What the game is doing.** The result is settled and printed, and then the Moderator writes the
closing story — one long call, the largest single piece of prose in the game.

**On screen today.** The one thing Graphia already does right: spec 040 arms a *"one moment…"*
hint on the log's border while the story is in flight and clears it when the story lands. It is
the only wait in the game that explains itself.

**Proposed remedy — keep it, and treat it as the template for the other four.** One caveat: it was
designed against a wait that is 10 s locally and is **64 s remotely**, so the same hint may need to
say more when the story is taking a minute.

## 2. The shape of the problem, in one table

| | local, all time | local, September | remote |
| --- | --- | --- | --- |
| waits measured | 480 | 61 | 40 |
| typical wait | 4.5 s | **12.8 s** | **17.5 s** |
| 9 in 10 under | 17.8 s | **71.0 s** | 50.5 s |
| worst | 205.4 s | 126.7 s | 77.9 s |
| waits ≥ 10 s | — | 60% | 58% |
| waits ≥ 30 s | — | **36%** | 23% |

More than a third of September's waits last half a minute or longer, and the player has no way to
tell any of them apart from a hang.

---

## 3. Half the waiting shows nothing

Splitting September's node time by whether the work produced anything the player could see:

| | seconds |
| --- | ---: |
| work that painted something | 908.7 |
| **work that painted nothing** | **972.6** |
| **silent share** | **51.7%** |

That 51.7% is a floor, not an estimate — messages addressed privately to another player are
dropped before they reach the pane, so some of the 908.7 s was invisible too.

The silence is concentrated in three places, and all three are silent *deliberately*:

| what it is | how long | why nothing appears |
| --- | --- | --- |
| everyone privately reflects on the round | 11 s typical, 42 s worst | private thoughts never enter the public stream |
| everyone writes their diary for the day | 25 s typical, 53 s worst | diaries are private to their author |
| everyone is given a persona at setup | 10 s local / **31 s remote**, 59 s worst | it happens before the game has printed anything |
| everyone reads their own diary back | ~8 s per night, **remote only** | a deliberate health check of the remote read path |

These are not bugs and the fix is not to make them public. They are the product working correctly
while the player is given no reason to believe it.

---

## 4. Why the waits are as long as they are

Each of those three is a fan-out: one model call per surviving AI player, and with the CR-007
lineup of 6 citizens + 2 mafia that is **seven calls**. They run **one after another** —
`day_round_reflect`'s loop (`src/graphia/nodes/day.py:1368`) iterates the players and calls
`_ai_reflect` serially; `day_diary` and `generate_personas` have the same shape. Seven sequential
calls at 1.5–4 s each is the 11–27 s the player sits through.

The node's own docstring explains why it is a separate step — *"a fan-out of N non-deterministic
`get_large` calls"* committed as its own super-step so an interrupt replay never re-runs it. That
reasoning is sound and is not in question here; what it does not address is that the step is
serial and mute.

Across the 40 remote invocations, total time by graph node:

```
 211.2s  n=178  day_turn            ← visible: AI speeches, ~1.2s each
 189.5s  n=20   day_round_reflect   ← silent
 153.6s  n=5    generate_personas   ← silent
  69.3s  n=9    night_close         ← silent; diary read-back, remote-only in size
  54.9s  n=2    day_diary           ← silent
  44.9s  n=60   collect_votes       ← visible
  18.5s  n=2    moderator_story     ← visible, and already narrated
```

---

## 5. Remote is worse, and worse in a different place

Remote medians are roughly 40% higher than local, but the gap is not evenly spread:

- **Setup nearly triples** — 16.3 s local → 43.8 s remote, driven almost entirely by persona
  generation (≈31 s per invocation remotely against ≈10 s locally).
- **`night_close` becomes significant** — 69.3 s across 9 invocations remotely, where the same
  node is a rounding error locally. It is not writing anything: it **reads each surviving player's
  diary back**, which remotely is a Gateway → Lambda → Memory round-trip per player (the trace
  shows 40 Gateway round-trips in a single invocation). Those reads feed nothing to the model and
  are kept on purpose, as the only live exercise of the remote read path — so remotely the player
  pays roughly 8 s per night for a health check.
- **The ending is six times worse** — 10.5 s local against 63.9 s remote.
- **Launching the app** costs 2.6 s typical and up to 13.6 s before the name prompt appears, in
  front of a blank screen. Locally it is instant.

---

## 6. Why September is the measurement that counts

| silent step | first appears in the log |
| --- | --- |
| personas | 2026-06-18 |
| private reflection | 2026-06-21 |
| **diaries** | **2026-09-04** |
| **closing story** | **2026-09-08** |

Two of the largest silent costs landed this month, on top of a lineup CR 007 raised to 6+2 — and
every fan-out costs one more serial call per player added. The all-time median of 4.5 s describes
a smaller, quieter game that no longer exists; the median wait has roughly tripled.

None of this is an argument for removing anything. Private reflection, diaries and the closing
story are the product. What is unaddressed is the cost they impose on the player's attention.

---

## 7. What the player is given today

One affordance exists, and it covers moment ⑤ only. Spec 040's design notes are worth keeping,
because they rule out the two obvious implementations:

- **Not a log row.** `RichLog` has no per-line removal, only `clear()`, so a "one moment…" row
  could never be taken back and would sit in the finished ending forever. The hint is painted on
  the widget's border instead, which costs zero rows and is retractable.
- **Not `widget.loading`.** It replaces the widget's content with a spinner — hiding the text the
  player is meant to be reading in exactly the window where reading it is the point.

Everywhere else, the input greys out with a `…` placeholder and the pane holds still.

---

## 8. What this does and doesn't establish

- **September's local sample is small** — 61 waits over a handful of sessions. The medians are
  indicative. The ranking of the silent steps is not sample-dependent: those steps are silent by
  construction, and the remote traces reproduce the same ordering independently.
- **"Silent" is inferred** from whether a step's update carried any message at all, not from what
  the pane actually painted. This understates the silence.
- **A step's duration** is the interval between consecutive updates, so checkpointing and ledger
  writes inside a step are counted against it.
- **Not measured:** time-to-first-token within a single speech (needs the Phase-6a `astream`
  switch), and whether the fan-outs are *safely* parallelisable — that is a design question, not a
  measurement.
- **Reproduce it:** `make show-trace ARGS="--list"` for the remote sessions; the local numbers come
  from the `resume_returned` / `request_resume` markers in `.graphia/graphia.log`.
