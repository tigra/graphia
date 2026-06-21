---
description: Quickly capture an ungroomed item into the product backlog (context/product/backlog.md).
argument-hint: <the idea / gap / follow-up to capture>
---

# /backlog — capture an ungroomed backlog item

You add ONE raw item to the project backlog at `context/product/backlog.md`. The backlog is
**ungroomed**: a fast place to note ideas, gaps, and follow-ups that are **not yet thought
through enough** for a spec or the roadmap. Your job is to **capture, not groom** — record the
idea faithfully and tersely; do not design it, expand its scope, or invent detail the user
didn't give.

## Input

The item to capture: **$ARGUMENTS**

If `$ARGUMENTS` is empty, ask the user in one line what they want to add, and stop until they answer.

## Steps

1. **Read** `context/product/backlog.md` to see its current sections and house style.
2. **Place it.** Choose the best-fitting existing section (e.g. *Follow-up specs — AI quality*,
   *Robustness gaps*, *Test reliability*, *Measurement / eval ideas*, *Housekeeping*,
   *Roadmap features*). If none fits, add a new section with a short heading, or use a general
   bucket — don't proliferate headings. Only if the right section is genuinely unclear, ask the
   user (offer the 2–3 candidate sections).
3. **Write one bullet** in the house style:
   - a short **bold lead-in**, then a 1–3 sentence description of the idea and why it matters;
   - end with an `_Origin: <source> <date>._` stamp — default the source to `user request`
     (or a clearer origin if the conversation gives one), and write the date **absolute**
     (resolve "today"/"now" to the real calendar date), matching the existing stamps.
   - Keep it terse — this is a note, not a spec. State genuine unknowns plainly rather than
     guessing; never add implementation detail the user didn't provide. If the item is clearly
     distinct from neighbouring ones, make it its own bullet (don't bundle unrelated concepts).
4. **Bump** the `_Last updated: <date>._` line near the top to today (absolute date).
5. Do **not** reorder, rewrite, or delete existing items.

## After capturing — offer escalation (don't act on it)

A backlog item sometimes deserves more than a note. In **one line each**, offer only what
clearly applies — these are suggestions, not actions:

- Revises a previously-agreed requirement (scope, roadmap order, success criteria)?
  → suggest `/buddah:change-request`.
- A load-bearing architectural choice (tech, vendor, deployment target, data store, security)?
  → suggest `/buddah:adr`.
- Already well-formed enough to become real work? → note it could graduate to `/awos:spec`
  or a `/awos:roadmap` entry later.

Do **not** invoke those commands or commit anything unless the user asks.

## Report

State which section the item landed in and show the bullet you wrote.
