# Functional Specification: The Moderator's Closing Story

- **Roadmap Item:** Phase 6a — **End-of-Game Payoff → Moderator Creative Recap**. The first item of that group; the three **Asynchronous Day Chat** items in the same phase are separate and out of scope.
- **Status:** Completed *(verified 2026-09-09 — all **36** acceptance criteria met — 35 at verification, plus one added by the 2026-09-09 amendment below and met by five new tests; suite green at **2845 passed / 1 skipped**. Two criteria hold by construction with nothing in the suite to witness them, and are logged in `context/product/backlog.md` rather than counted as coverage: `ctrl+c` at game-over has no test anywhere (coverage is mid-game only, and the tech spec's §4 line omits the key the criterion names), and no forced-path test asserts the career panel on an ending whose story failed. The three criteria about **a game whose story cannot be told** are satisfied by a **forced** failure rather than a natural instance — this spec's own design makes the real failure rare (record 45: `story_missing_rate: 0.0`). And the one live clamp fired on the **character backstop**, not on the paragraph bound the criterion states, so the stated limit remains witnessed only by constructed input, exactly as Task 5.4 predicted — **and that pair has since been recalibrated by the amendment below**, which raised the stated bound to five paragraphs and the backstop to 2500 characters so the stated limit governs again. **AMENDED 2026-09-09 AFTER VERIFICATION, at the author's request and by their ruling of amendment over change request: the story is now coloured distinctively, and it is longer.** Three requirements carry dated amendment notes for it, and the count above moved by one.)*
- **Author:** Alexey Tigarev

> **This is the payoff spec 039 deliberately withheld.** **039 (Per-AI Private Diaries)** built the private diaries this story reads, and shows the person playing **nothing** at end-of-game — the author's explicit decision at the time, taken because the story that consumes them is this separate item. Spec 039 is complete; its diaries are written before each Night and have never once been shown to a player.

> **Two decisions taken during technical review, both recorded here because they change what the person sees.** The story is delivered as **its own message** after the facts rather than appended to them — cleaner to identify everywhere, at the cost of a second `Moderator:` beat mid-ending and four existing tests to port. And **leaving the game becomes a specific key** rather than any key, because the ending no longer fits on one screen and the facts would otherwise scroll away with no way back.

> **A finding that makes the two-channel decision load-bearing rather than generous.** The whole-day diaries are absent for Night 1, for a runaway game's final Day, and — most importantly — for **the Day the game is won**, because a winning move ends the game before that day's diaries are written. That is the day a story most wants to land its reveal on. The shorter end-of-round reflections *do* cover the winning day's completed rounds. So drawing on both private channels is not a richness choice; without the reflections the climactic day has no private material at all.

> **What the ending already gives away, and why that shapes this spec.** When a game ends the person already sees the winner, every death, the full roster with every role, and who each AI player really was — including each Mafioso's cover story set against its true self. So this story cannot earn its place by disclosing roles or outcomes; those are on screen a moment earlier. It earns its place from the **private material**: what the AI players privately wrote and never said aloud.

---

## 1. Overview and Rationale (The "Why")

A game of Graphia ends with the facts. Who won, who died, who everyone really was. It is complete, and it is a list.

What it cannot tell is the story. Which player privately decided, two days before anyone said it aloud, that the baker was lying. Which Mafioso spent a whole day steering suspicion onto a person they knew was innocent. Which citizen came within one vote of catching the conspiracy and then talked themselves out of it. All of that happened, all of it was written down, and **the person playing has never seen a word of it** — the AI players' private diaries and their private end-of-round reflections are hidden throughout play by design.

This change gives the Moderator the last word. After the facts, they tell a **short story about the game just played**, drawing on that private material to reveal what was really going on behind the conversation the person sat through.

**The story is held to the record.** It retells the real game — real players, real deaths, real votes, real private writing — and does not invent events, players or motives nobody recorded. Its creativity is in the telling: which thread to follow, what to set beside what, where to land the reveal. A story that fabricates would be worse than no story at all, because the person has no way to tell invention from record and will simply believe it.

**The story is also kept.** It is preserved in the record of a measured game alongside everything else that happened, so a reviewer can read it later and judge whether it was any good — and so the quality of these stories becomes something the project can look at over time rather than something each player experiences once and forgets.

**Success looks like:** a person finishes a game, reads the facts, and then reads a few paragraphs that tell them something they did not know — grounded in what the AI players privately wrote, about the game they just played, with their own seat in it; the story never claims to know what the *person* was thinking, because nothing recorded that; a game whose story cannot be produced still ends exactly as it does today; and a reviewer can find the story in the preserved record of a measured game and judge it.

---

## 2. Functional Requirements (The "What")

- **When a game ends, the Moderator tells a short story about it.**
  - After the closing facts — the winner, the deaths, the roster, who each AI player really was — the Moderator delivers a short story about the game just played. The career summary follows the story, as it does today. **The story is shown in its own colour**, so the person can see at a glance that this is a telling rather than more of the record.
  - **Acceptance Criteria:**
    - [ ] Given a game that has just ended, when the person reads the closing screens, then a story about the game appears after the roles-and-characters reveal and before the career summary.
    - [ ] Given the closing screens, when the person reads them in order, then the facts come first and the story second — the story never appears above the reveal.
    - [ ] Given a game that ends because one side won, and a game that ends any other way it can end, then a story appears in each case.
    - [ ] Given the closing screens, when the person looks at them, then the story is visually distinct from the Moderator's factual announcements and from what players said during the game — and the distinction is visible across the story itself, not only on its first few characters.

  > **AMENDED 2026-09-09, AFTER THIS SPEC WAS VERIFIED, AT THE AUTHOR'S REQUEST.** The colour sentence and the fourth criterion are new; the author asked for the recap to be coloured distinctively in the UI. It is recorded here rather than as a change request because the author ruled amendment over CR for this spec, which is the current one. **Why it needed asking for at all:** the story arrives as a Moderator announcement exactly like the winner line and the closing facts, so on message type alone it rendered in the same colour as the record it must not be mistaken for. **What "and the distinction is visible across the story itself" is doing in the criterion:** colouring only the `Moderator:` label would satisfy a looser wording while leaving several paragraphs of prose indistinguishable from any other body — so the criterion names the body, and the test for it asserts the span count rather than a colour name, which survives any restyle that keeps the body styled.

- **The story draws on what the AI players privately wrote and never said aloud.**
  - Every AI player's private diaries and private end-of-round reflections are available to the story — those of the players who died **and** those who survived. Both are material the person has never seen.
  - **Acceptance Criteria:**
    - [ ] Given a finished game in which AI players wrote privately, when the story is read, then it draws on that private writing — it is not a retelling of only what was said aloud.
    - [ ] Given a game the Mafia won, when the story is read, then the surviving Mafiosos' private writing is available to it — the conspiracy that won is not sealed off.
    - [ ] Given a game in which a player died early, when the story is read, then that player's private writing, **if any exists**, is available to it too — nothing is withheld on the grounds that they did not survive. *(Note, verified — **with the mechanism corrected 2026-09-08, spec 040 Task 3.6:** a player killed on the very first Night wrote nothing at all, so for the earliest deaths there is genuinely nothing to draw on. The discriminator is **not** "they die before the first day's talk" — a Night-**2** victim also dies before Day 2's talk and carries a full Day-1 record. It is that **no Day round has closed** before Night 1's death: both writers gate on `is_alive and not is_human`, and `day_round_reflect` additionally fires only at a fresh round start. The wrong mechanism licenses a false generalisation — that a player **executed on Day 1** also wrote nothing. Usually they did: reflections accumulate at every completed round and the execution happens afterwards, so only the diary is missing. Nothing filters by survival; the material simply may not exist.)*
    - [ ] Given a finished game, when the story is read, then it may draw on both the whole-day diaries and the shorter end-of-round reflections.

- **The story stays to what actually happened.**
  - The story is asked to retell the real game and told not to invent events, players or motives that nobody recorded. It may choose what to dwell on and how to tell it; it may not make things up.
  - **Acceptance Criteria:**
    - [ ] Given the request the Moderator is given, when it is read, then it tells the story to stay to what happened and not to invent events, players or motives.
    - [ ] Given a story about a finished game, when a reviewer compares it against that game, then the people, deaths and votes it names are ones that really occurred.
    - [ ] Given the story is asked for, when the request is read, then it is free to choose which thread to follow and how to tell it — staying to the record is not a demand for a flat summary.

  > **An honest limit, stated rather than glossed.** Instructing a storyteller is not the same as guaranteeing the story. These criteria require that the instruction is given and that the story is grounded in real material; they do not promise that no sentence is ever embellished. Preserving the story (below) is what lets that be judged over time instead of assumed.

- **The story is a few short paragraphs, and the limit holds.**
  - The story is long enough to follow a thread and land a reveal, and short enough to read at the end of a game. An upper limit is stated when the story is asked for, and it is applied to the story that comes back.
  - **Acceptance Criteria:**
    - [ ] Given the request the Moderator is given, when it is read, then it states an upper limit on the length of the story.
    - [ ] Given a story that has been produced, when its length is checked, then it does not exceed that limit — **applied when the story is taken in, not merely asked for**. A limit stated only in the request is a request.

  > **AMENDED 2026-09-09, AFTER VERIFICATION, AT THE AUTHOR'S REQUEST — the limit was raised, and neither criterion above moved.** The author asked for a longer story. The stated bound went from **four short paragraphs to five**, and the backstop that runs beside it from **1800 to 2500 characters**. Both criteria are worded on *"an upper limit"* rather than on a number, so they read on the new pair unchanged — which is why this is an amendment note rather than a rewrite.
  >
  > **The raise also fixed a miscalibration this spec's own verification found.** At 4 and 1800 the *character* backstop was quietly governing a bound stated in *paragraphs*: the one story measured live came in at three paragraphs of 474 + 487 + 419 characters and was clamped because a fourth ~460-character paragraph would have crossed 1800, while the stated cap of four had not been reached. At 5 and 2500 the paragraph bound governs and the backstop is a runaway guard again.
  >
  > **And the new number has a ceiling that is not a matter of taste.** The local model's output allowance is 1024 tokens — roughly 3000 characters of response once the JSON wrapper and escapes are counted — so anything near it is reached by a provider truncation rather than by a long story, and the story would lose its last sentence with nothing to distinguish that from an ending the storyteller chose. 2500 is the top of the documented margin under that ceiling, and 2500 divided by the paragraph length real stories come in at is what makes the stated bound five rather than six. Going further means widening that allowance first.

- **The person's own seat is in the story, from what they did in public.**
  - The story may talk about the person's seat — what they said, how they voted, who they went after — because all of that was public. It has no private writing for them, and never claims to know what they were thinking.
  - **Acceptance Criteria:**
    - [ ] Given a finished game, when the story is read, then the person's seat may appear in it as one of the players.
    - [ ] Given the story mentions the person's seat, when it does, then it speaks only of what that seat did in the open — never of private thoughts or plans it attributes to them.
    - [ ] Given a measured game, where the person's seat is played by an automated stand-in, when the story is read, then that seat is treated the same way as a person's would be.

- **The story is preserved in the record of a measured game.**
  - The records kept of measured games include the story, alongside everything else that happened in that game, so a reviewer can read it later — and judge whether it was worth reading.
  - **Acceptance Criteria:**
    - [ ] Given a measured game in which a story was told, when its preserved record is read, then the story appears in it.
    - [ ] Given the story in that record, when a reviewer looks at it, then it is clearly marked as the Moderator's closing story rather than as something said during play.
    - [ ] Given the story in that record, when a reviewer looks at where it sits, then it is at the end, after the game's final events.
    - [ ] Given a reviewer reading a preserved game, when they compare the story against the private writing it drew on, then both are readable in the same record without going elsewhere.

- **A game whose story cannot be told still ends properly.**
  - If the story cannot be produced, that section is simply absent. Everything else about the ending appears exactly as it does today, the person is never shown an error, and the game never stops part-way through its ending.
  - **Acceptance Criteria:**
    - [ ] Given a game whose story cannot be produced, when the person reads the closing screens, then the winner, the deaths, the roster, the characters and the career summary all appear as they normally do.
    - [ ] Given that same game, when the person reads the closing screens, then no error and no apology about a missing story is shown.
    - [ ] Given that same game, when it ends, then it ends completely — it does not stop part-way through the ending.

- **The person can read back through the ending before leaving.**
  - The ending is now longer than one screen — the facts, then the story. Today the game exits on **any** keypress once it is over, so a person cannot look back at the winner, the deaths, the roster or the characters once the story has pushed them out of view. Leaving must become a deliberate act, so the person can scroll back through the whole ending first.
  - **Acceptance Criteria:**
    - [ ] Given a finished game whose ending is longer than the screen, when the person scrolls up, then they can read the earlier parts of it — the winner, the deaths, the roster and the characters reveal.
    - [ ] Given a finished game, when the person presses the **advertised** leaving key, then the game closes.
    - [ ] Given a finished game, when the person presses **`escape`** or **`ctrl+c`**, then it still leaves — this adds an advertised key, it does not withdraw the ones people know. *(Wording corrected 2026-09-09, Slice 6: this criterion previously said "one of the keys that **already** left the game before this change", which is **unsatisfiable as written** alongside the two criteria below — every key left before this change, via `on_key`. The two keys people actually know are named instead. Note for whoever verifies it: `escape` at game-over leaves in **two** keystrokes, not one — it pushes `QuitModal` and `y` then exits.)*
    - [ ] Given a finished game, when the person presses an ordinary letter key, then the game stays open and they remain able to read the ending.
    - [ ] Given a finished game, when the person presses **the space bar**, then the game stays open — space is named explicitly because it exits today and is what people press to page through text.
    - [ ] Given a finished game, when the person scrolls with the **mouse wheel**, then the ending scrolls and the game stays open.
    - [ ] Given a finished game, when the person looks at the screen, then it is clear which key leaves — and that reminder is still visible after they have scrolled up, not only at the bottom of the ending.

- **While the story is being produced, the person can see that something is coming.**
  - The story takes a moment to write. That moment sits between the facts and the story, at the very end of a game, and without any sign of activity it reads as a hang — a person who gives up and quits there loses the story entirely.
  - **Acceptance Criteria:**
    - [ ] Given a finished game whose story is still being produced, when the person looks at the screen, then something tells them the story is coming.
    - [ ] Given the story then arrives, when the person reads the finished ending, then **nothing shown during the wait remains in it** — the ending contains the facts, the story and the career summary, and no leftover "please wait" text.
    - [ ] Given a game whose story cannot be produced at all, when the ending finishes, then the same is true: no leftover waiting text remains.

- **Everything else about the ending is unchanged.**
  - The winner announcement, the list of deaths, the roster, the characters reveal and the career summary all read exactly as they did before. This adds a section; it removes and rewords nothing. **Three deliberate exceptions.** First, how the game is left — see above: the ending no longer fits one screen, so leaving gains an advertised key. Second, **a display fault this story forces us to fix**: today, text in square brackets is silently deleted from what players say on screen — `he called it [gibberish] and moved on` renders as `he called it and moved on`, with no error and no trace — and some bracket shapes crash the ending outright, which would skip the career summary entirely. A several-paragraph story is by far the largest target the game has ever put on that path, so it is fixed here. The visible consequence is an improvement, but it **is** a difference: bracketed text that used to vanish from players' lines will start appearing.
  - **Acceptance Criteria:**
    - [ ] Given a person who played before this change, when they finish a game after it, then every closing section they knew is still there, in the same order, saying the same things.
    - [ ] Given a finished game, when the person reads the closing screens, then the only differences are the story between the characters reveal and the career summary, that the story is shown in its own colour, that leaving now has an advertised key, and that bracketed text is no longer silently dropped from what players say.

  > **AMENDED 2026-09-09, AFTER VERIFICATION, AT THE AUTHOR'S REQUEST — this enumeration now names FOUR differences, not three.** The story's own colour is the added one, and unlike the other two it is not a fault this story forced us to fix: the author asked for it after the spec was verified. It is called out here rather than left to be discovered, because *"the only differences are…"* is the one criterion in this spec that a later change silently falsifies. Nothing else in this requirement moved: every closing section is still there, in the same order, saying the same things, and the story's colour changes no text at all — the row a reader sees is character-for-character what it was, so every check that reads the closing screens as text measures exactly what it measured before.
    - [ ] Given a story containing square brackets, when the ending is shown, then the brackets appear as written, the career summary still appears, and no error is shown.

---

## 3. Scope and Boundaries

### In-Scope

- A short story about the game just played, told by the Moderator after the closing facts and before the career summary.
- Drawing on every AI player's private diaries **and** private end-of-round reflections — those who died and those who survived alike.
- Holding the story to what actually happened, and saying plainly that this is an instruction rather than a guarantee.
- A stated length limit that is applied to the story that comes back.
- Including the person's seat as a character, from public record only.
- Preserving the story in the record of a measured game so it can be read and judged later.
- Ending the game properly when no story can be produced.

### Out-of-Scope

- **Showing the private writing itself to the person.** The story draws on it; the diaries and reflections are not printed for the player. Spec 039 settled that, and this spec does not reopen it.
- **Changing, rewording or removing any existing closing section**, or the order they appear in.
- **Changing how the private writing is produced, stored or fed back into play** — spec 039 and spec 028 own that.
- **Judging the story automatically.** This preserves it so it *can* be judged; a whole-transcript judge is a separate Phase 7 item.
- **Giving the Moderator a structured set of helper requests** for fetching the material — the roadmap places that in Phase 7 and this spec has the Moderator read what it is given.
- **The three Asynchronous Day Chat items** in the same phase — concurrent AI chatter, concurrent human typing, and the vote-locks-chat handoff.
- **Any story or summary during play** — this is the ending only.
- All other roadmap items, which are automatically out-of-scope for this specification.
