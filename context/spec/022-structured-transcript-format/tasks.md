# Tasks: Structured Eval-Transcript Format (Spec 022)

Display-only renderer change (no gameplay effect → no ablation flag, ADR 011 exempt).

> **Note:** no `technical-considerations.md` (fast-tracked past `/awos:tech`); the
> implementation approach is folded into the tasks below. The renderer is the pure
> `src/graphia/tools/eval_transcript.py` (the same module specs 017 and 021 touched).

Functional spec: `./functional-spec.md`

---

- [ ] **Slice 1: The eval transcript renders as consistent, structured blocks**
  - [ ] Restructure the eval-transcript renderer (`src/graphia/tools/eval_transcript.py`): render a vote as one delimited `<vote initiator=… target=…>` block — each ballot a plain `Name: Yes/No` line (**drop the `Moderator:` prefix**), then the tally and the outcome (failed, or the executed player + revealed side); render the **night-kill** outcome as its own inline `<kill>…</kill>`; render the **end-of-round recap** as its own labeled element (e.g. `<recap>…</recap>`, content reproduced as-is, including the spec-020 in-world clock); render **setup** as structured per-player entries (name, role, persona) with no deep indentation; group the **endgame** (winner + full roster + persona reveal) in one labeled block. Apply uniform formatting: **flush-left / zero indent**, one-line sections written inline as `<tag>…</tag>`, multi-line sections delimited on their own lines; keep player **utterances** as plain `Name: text` lines. No information is lost vs today (preserve round labeling from spec 021). **[Agent: langgraph-agentic]**
  - [ ] Update + add tests in `tests/test_eval_transcript.py` over synthetic event logs: the vote block (ballots `Name: Yes/No`, no `Moderator:` prefix, tally, outcome — plus an executed-vote naming the revealed side); the night-kill inline block; the recap element being distinct from utterances and carrying day/clock/counts/votes/executed; the per-player setup with no alignment indentation; the endgame block (winner + roster + reveal); flush-left / no-alignment-spaces + inline one-liners; utterances unchanged; and a no-information-lost check (same speeches/ballots/kills/recaps/reveals present). Update the existing structural-tag / round-label / chronological-order tests for the new format. **[Agent: testing]**
  - [ ] Verification: `uv run pytest -q` green (confirm `tests/test_dual_mode_smoke.py` is unaffected — the eval transcript renderer is separate from the in-game public log); render one synthetic multi-round game (a vote, a night kill, per-round recaps, an endgame) and eyeball the block structure and flush-left formatting. **[Agent: testing]**
