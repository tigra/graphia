"""Graphia Textual app: private Moderator panel alongside public chat."""

from __future__ import annotations

import asyncio
import os
import threading
import traceback
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from graphia.career_events import (
    CareerEvent,
    CareerEventEmitter,
    make_career_emitter,
)
from graphia.config import GraphiaConfig, load_config
from graphia.driver import drive_graph
from graphia.graph import build_graph, make_run_config
from graphia.logging import StreamTraceLogger, setup_logger
from graphia.state import CLOSING_STORY_KWARG
from graphia.stats_store import (
    StatsStore,
    make_stats_store,
    render_greeting,
    render_panel,
    summarize,
)
from graphia.ui.badge import CornerBadge
from graphia.ui.failure_modal import FailureModal
from graphia.ui.quit_modal import QuitModal
from graphia.ui.widgets import PointingModal, VoteModal


EXIT_HINT = "Press q to leave."
"""The leaving affordance, in one place so it cannot drift.

Spec 040 (The Moderator's Closing Story), Slice 6. Used by all three
game-over banners AND by ``#public-log``'s border subtitle, which is the copy
that matters: a banner is a log row, so it scrolls out of view the moment the
person scrolls up — precisely when they need to know how to get out. A border
subtitle is painted on the widget frame, so it stays put for as long as they
read, and it costs **zero** log lines (measured: ``len(RichLog.lines)``
unchanged across the assignment).

Public and importable on purpose: a test that copies this literal would keep
passing after a reword and silently stop measuring the shipped affordance.
"""


def _content_to_text(content: Any) -> str:
    """Normalise a message `content` (str | list[block]) to a flat string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts).strip()
    return str(content)


def _speaker_of(msg: AIMessage) -> str:
    name = getattr(msg, "name", None)
    if isinstance(name, str) and name:
        return name
    extra = getattr(msg, "additional_kwargs", {}) or {}
    speaker = extra.get("speaker")
    if isinstance(speaker, str) and speaker:
        return speaker
    return "Someone"


class GraphiaApp(App[None]):
    CSS = """
    Screen { layout: vertical; layers: base overlay; }

    #header-bar {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text;
    }

    #private-log {
        height: 6;
        padding: 0 1;
        border: round $accent;
        margin-bottom: 1;
    }

    #public-log {
        height: 1fr;
        padding: 0 1;
        border: round $panel;
    }

    #player-input {
        height: 3;
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("escape", "request_quit", "Quit", show=True, priority=True),
        Binding("ctrl+c", "abort", "Abort", show=False, priority=True),
        # Spec 040 Slice 6: the named leaving key for a finished game. NOT
        # `priority=True` — a priority binding would steal `q` from the
        # docked Input and no player could type a name or a speech
        # containing the letter. With the default priority the focused
        # Input consumes printable keys first, and the binding is only ever
        # reached when focus is on a pane, which is where a finished game
        # leaves it. `show` is irrelevant either way: there is no Footer in
        # this app, so nothing renders BINDINGS (the affordance is
        # `#public-log`'s border subtitle — Task 6.4).
        Binding("q", "leave", "Leave", show=False),
    ]

    config: GraphiaConfig
    logger: StreamTraceLogger

    def __init__(
        self,
        stats_store: StatsStore | None = None,
        career_emitter: CareerEventEmitter | None = None,
    ) -> None:
        super().__init__()
        # Loaded eagerly so `compose()` can read `remote_mode` for the badge
        # label. `on_mount` reuses the same instance instead of reloading.
        self.config = load_config()
        # Injectable for tests, mirroring the graph's `diary_store=` seam;
        # built from config in `_drive` when not supplied.
        self._stats_store: StatsStore | None = stats_store
        # Same injection seam for the per-action career-events emitter; built
        # from config in `_drive` when not supplied and shared with the graph
        # so the quit path and the nodes target the same sink.
        self._career_emitter: CareerEventEmitter | None = career_emitter
        self._pending_resume: asyncio.Future[Any] | None = None
        self._human_id: str | None = None
        self._graph: CompiledStateGraph | None = None
        self._run_config: dict | None = None
        # LangGraph thread_id for this session, set in `_drive` once
        # `build_graph` returns it. Used by the remote-mode crash handler to
        # build the CloudWatch `{ $.thread_id = "<thread>" }` filter. None
        # until the graph is built (a crash that early shows no thread id).
        self._thread_id: str | None = None
        self._private_buffer: list[BaseMessage] = []
        # Running mirror of graph state, fed by the driver's on_state callback
        # from each super-step's update delta. Mode-agnostic: the streamed
        # chunks carry the same data locally and remotely, so reading the
        # mirror works even though graph.get_state() is empty in remote mode.
        self._latest_state: dict = {}
        # Track the cycle we last showed the "/vote <name>" hint for, so we
        # print it at most once per Day. None until the first observation.
        self._vote_hint_shown_for_cycle: int | None = None
        # Flips to True once the graph has reached END (either a winner was
        # announced or an unrecoverable error was caught). It is the gate on
        # `action_leave`: while False, `q` is passed back to Textual's
        # binding chain, so it stays an ordinary character the player can
        # type into their name or their speech. Once True, `q` leaves.
        self._game_over: bool = False
        # Flips to True once this game has been folded into the career — by
        # either the normal-end path (_record_career) or the abandoned-quit
        # path (_on_quit_decision). Guards against a double-record when the
        # player presses Esc after a normal end already recorded the game.
        self._career_recorded: bool = False
        # Flips to True the first time we observe the human's PlayerState
        # flipped to is_alive=False. Spectators still see the public log
        # and end-game screen but are never prompted and no longer receive
        # private Moderator whispers (defensive — in current mechanics the
        # Mafia-private intros only fire Night 1).
        self._spectator: bool = False

    def compose(self) -> ComposeResult:
        label = "[remote]" if self.config.remote_mode else "[local]"
        yield CornerBadge(label)
        with Vertical():
            yield Static("[b]Graphia[/b] [dim]Mafia, by candlelight[/dim]", id="header-bar")
            yield RichLog(id="private-log", highlight=False, markup=False, wrap=True)
            yield RichLog(id="public-log", highlight=False, markup=False, wrap=True)
            yield Input(placeholder="…", id="player-input", disabled=True)

    def on_mount(self) -> None:
        self.logger = setup_logger(self.config)
        private = self.query_one("#private-log", RichLog)
        private.border_title = "Whispers (only you see this)"
        self.run_worker(self._drive(), exclusive=True, name="graphia-drive")

    async def _on_graph_state(self, update: dict) -> None:
        """Mirror a streamed super-step update delta into ``_latest_state``.

        Shallow merge is correct: ``human_id`` and ``players`` are both
        whole-value-replace channels (no reducer accumulation), so each
        chunk carries the complete current value for any key it touches.
        """
        self._latest_state.update(update)

    def _refresh_human_id(self) -> None:
        """Pick up `human_id` from the streamed-state mirror as soon as it's set."""
        if self._human_id is not None:
            return
        value = self._latest_state.get("human_id")
        if isinstance(value, str) and value:
            self._human_id = value
            # Flush any private-tagged messages that arrived before we knew the id.
            if self._private_buffer:
                buffered = self._private_buffer
                self._private_buffer = []
                for msg in buffered:
                    self._write_private(msg)

    def _write_private(self, msg: BaseMessage) -> None:
        """Write one private whisper to the whispers pane, as styled spans.

        Spans rather than markup for the reasons argued at
        :meth:`_write_public` — and the exposure here is the same in kind, not
        a lesser one: this pane carries the human's role reveal and the Mafia
        teammate intros, prose built by the same nodes and, for the personas
        woven into them, by the same model.
        """
        log = self.query_one("#private-log", RichLog)
        body = _content_to_text(msg.content)
        log.write(
            Text.assemble(("Moderator (private): ", "bold magenta"), (body, ""))
        )

    def _write_public(self, msg: BaseMessage) -> None:
        """Write one graph message to the public pane, as STYLED SPANS.

        **A message body is never handed to ``Text.from_markup``** (spec 040 —
        The Moderator's Closing Story, Slice 2, fixing a fault that predates
        it). Interpolating a body into a markup string hands Rich's tag parser
        text nobody authored, and it does two different wrong things with it.
        Both measured on the installed Rich, against this exact label:

        - ``'he called it [gibberish] and moved on'`` renders as
          ``'he called it  and moved on'`` — **silently deleted**: no error, no
          trace, and the player never learns a word went missing. This is the
          insidious half. It is also **case-sensitive** — Rich only treats a
          bracket as a tag when its body opens with a lowercase letter, ``#``,
          ``/`` or ``@`` — so ``[Gibberish]`` and ``[Sic]`` survive untouched
          and a capitalised probe passes against unfixed code and proves
          nothing.
        - ``'a stray [/bold] close'`` and ``'a stray [/] close'`` raise
          ``MarkupError``, which is the half that costs more than it looks. It
          propagates :meth:`_write_public` -> :meth:`_handle_graph_message` ->
          ``driver._consume_stream`` -> ``driver.drive_graph`` -> :meth:`_drive`'s
          ``except``, so :meth:`_record_career` is never reached and a red
          banner replaces the ending — defeating spec 040's designed no-error
          failure path from the one side the story node's own guards cannot
          reach.

        ``Text.assemble`` attaches each style to a character *range* instead of
        parsing it out of the text, so both halves go away at once: the label
        keeps its colour and the body renders exactly as written. This is spec
        038 (Colour-Coded Transcript Reading View)'s answer, already
        load-bearing in ``ui/ledger_viewer.py``, and the ``markup=False`` both
        panes are already built with keeps the string path closed behind it.

        **Nothing is lost by the switch**, checked rather than assumed: no
        message body in ``prompts.py`` or ``nodes/`` contains markup, and all
        393 committed transcripts contain zero lowercase-bracket sequences. The
        visible consequence is one-way — bracketed text that used to vanish
        from players' lines now appears, which the functional spec lists as a
        deliberate difference.

        The project's OWN literals — labels, hints, banners — stay on
        ``from_markup`` elsewhere in this file: those are authored strings that
        legitimately carry markup. The line drawn is *authored literal* versus
        *model or player content*, and every value interpolated here is the
        latter: the body, and the speaker name (a roster name from the small
        model, or the one the person typed at the name prompt, neither of them
        sanitised for brackets).
        """
        log = self.query_one("#public-log", RichLog)
        body = _content_to_text(msg.content)
        if isinstance(msg, SystemMessage):
            label, style = "Moderator: ", "bold cyan"
        elif isinstance(msg, AIMessage):
            label, style = f"{_speaker_of(msg)}: ", "bold"
        else:
            return
        extra = getattr(msg, "additional_kwargs", {}) or {}
        if extra.get(CLOSING_STORY_KWARG):
            # Belt-and-braces at the point of no return (spec 040 §2.11).
            #
            # DELIBERATELY NARROW — this guard wraps the closing story and
            # nothing else, because the story is the one write whose failure
            # costs more than itself. It is the last graph message of the game,
            # so anything raising here unwinds past ``_record_career`` and the
            # person gets a banner where the career panel should be; every
            # other beat is mid-game and re-renders next turn.
            #
            # It is NOT the bracket fix. The span path above cannot raise on
            # markup any more, and this catch would be pointless if that were
            # the only hazard. What it covers is the residue: a
            # several-paragraph model-written body is by far the largest string
            # this pane has ever taken, and the fallback keeps the ending whole
            # by giving up the styling rather than the story.
            try:
                log.write(Text.assemble((label, style), (body, "")))
            except Exception:  # noqa: BLE001
                log.write(Text(f"{label}{body}"))
            return
        log.write(Text.assemble((label, style), (body, "")))

    def _check_spectator_transition(self) -> None:
        """Detect the human's death from the streamed-state mirror.

        Inspecting ``PlayerState.is_alive`` is more robust than scanning
        messages because the authoritative flip happens inside the night
        resolver / vote tally, and the public Moderator announcement may
        phrase the death in many ways. Called once per incoming message.
        """
        if self._spectator or self._human_id is None:
            return
        players = self._latest_state.get("players")
        if not isinstance(players, dict):
            return
        me = players.get(self._human_id)
        if me is None or getattr(me, "is_alive", True):
            return
        self._spectator = True
        private = self.query_one("#private-log", RichLog)
        private.write(
            Text.from_markup(
                "[bold yellow]You have been killed. "
                "Watching as a spectator.[/]"
            )
        )
        private.write(
            Text.from_markup("[dim](Press Esc to exit.)[/dim]")
        )
        public = self.query_one("#public-log", RichLog)
        public.write(Text.from_markup("[dim](You are now spectating.)[/dim]"))

    async def _handle_graph_message(self, msg: BaseMessage) -> None:
        self._refresh_human_id()
        self._check_spectator_transition()
        extra = getattr(msg, "additional_kwargs", {}) or {}
        private_to = extra.get("private_to")
        if isinstance(private_to, str) and private_to:
            if self._human_id is None:
                # Race: buffer until we learn our id (which will happen shortly).
                self._private_buffer.append(msg)
                return
            if private_to == self._human_id:
                if self._spectator:
                    # Dead players lose their private Mafia channel. Drop to
                    # the JSONL trace for postmortem visibility but never
                    # surface in the TUI.
                    self.logger.record(
                        {"dropped_private": True, "to": private_to}
                    )
                    return
                self._write_private(msg)
            # Silently drop messages addressed to a different player.
            return
        self._write_public(msg)

    async def _prompt_via_input(self, placeholder: str) -> str:
        """Enable the docked Input, await one submission, disable again."""
        if self._spectator:
            # Should never fire: dead players aren't in day_order / vote
            # pending / night_picks, so no interrupt targets them. Raising
            # here surfaces any graph-logic regression via the worker-error
            # handler instead of silently prompting a spectator.
            raise RuntimeError("Spectator cannot be prompted")
        prompt = self.query_one("#player-input", Input)
        prompt.placeholder = placeholder
        prompt.disabled = False
        prompt.focus()
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_resume = fut
        try:
            value = await fut
        finally:
            self._pending_resume = None
            prompt.disabled = True
            prompt.value = ""
        return value if isinstance(value, str) else str(value)

    def _current_cycle(self) -> int | None:
        """Read the current ``cycle`` (1-based Day index) from the streamed state.

        Returns None if no cycle has streamed yet. Sourced from
        ``_latest_state`` (mirrored from graph stream chunks) rather than
        ``graph.get_state`` so it works in remote mode too — the local
        graph is empty when the game runs in the deployed Runtime. Used to
        gate the once-per-Day ``/vote`` hint.
        """
        value = self._latest_state.get("cycle")
        return value if isinstance(value, int) else None

    async def _request_resume(self, payload: dict) -> Any:
        kind = payload.get("kind")
        if kind == "name":
            return await self._prompt_via_input("Enter your name…")
        if kind == "day_turn":
            speaker_name = str(payload.get("speaker_name") or "You")
            log = self.query_one("#public-log", RichLog)
            raw_error = payload.get("error")
            error = raw_error if isinstance(raw_error, str) and raw_error else None
            if error:
                # Whole-line style, not markup. The string itself is one of the
                # graph's own authored literals ("Usage: /vote <name>", "No such
                # player. Try again.") — but this call site cannot see that, and
                # the day the hint quotes back what the person typed it would
                # re-open the bracket fault with no change here to notice. A
                # style on the whole ``Text`` is exactly what the tags meant.
                log.write(Text(error, style="bold red"))
            # ``speaker_name`` is the name the person typed at the name prompt,
            # taken as ``value.strip()`` with no bracket sanitising — so a
            # player calling themselves ``[/]`` used to crash their own first
            # turn here, and one calling themselves ``[sic]`` had the name
            # deleted from the line announcing it.
            log.write(Text(f"It's your turn, {speaker_name}.", style="dim"))
            # Show the /vote hint once per Day. If the cycle can't be read
            # (shouldn't happen here), fall back to never repeating.
            cycle = self._current_cycle()
            if cycle is not None and cycle != self._vote_hint_shown_for_cycle:
                log.write(
                    Text.from_markup(
                        "[dim](Type /vote <name> to call a vote to "
                        "execute someone.)[/dim]"
                    )
                )
                self._vote_hint_shown_for_cycle = cycle
            text = await self._prompt_via_input(
                f"{speaker_name}, it's your turn. Speak…"
            )
            stripped = text.strip()
            # Keep DayAction.text non-empty; a no-op turn becomes an ellipsis.
            return stripped if stripped else "…"
        if kind == "point":
            options = payload.get("options") or []
            # Spec 015 §2.5: thread the multi-round context through to the
            # modal — the current round, the cap, and the by-name picks-so-far
            # summary the graph rendered. All optional with safe defaults, so a
            # round-agnostic call (or any older payload) still works.
            raw_round = payload.get("round")
            round_number = raw_round if isinstance(raw_round, int) else None
            raw_cap = payload.get("round_cap")
            round_cap = raw_cap if isinstance(raw_cap, int) else None
            raw_prior = payload.get("prior_picks")
            prior_picks = raw_prior if isinstance(raw_prior, str) and raw_prior else None
            # push_screen_wait blocks this worker until dismiss() is called;
            # PointingModal resolves with the selected target's .id (str),
            # which is exactly what the graph interrupt expects as the
            # Command(resume=...) value.
            target_id = await self.push_screen_wait(
                PointingModal(
                    options=options,
                    round_number=round_number,
                    round_cap=round_cap,
                    prior_picks=prior_picks,
                )
            )
            return target_id
        if kind == "vote":
            target_name = str(payload.get("target_name") or "this player")
            raw_error = payload.get("error")
            error = raw_error if isinstance(raw_error, str) and raw_error else None
            # VoteModal dismisses with the literal "yes" or "no" — exactly
            # the strings collect_votes accepts as Command(resume=...).
            result = await self.push_screen_wait(
                VoteModal(target_name=target_name, error=error)
            )
            return result
        raise NotImplementedError(f"Unhandled interrupt kind: {kind}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "player-input":
            return
        fut = self._pending_resume
        if fut is None or fut.done():
            return
        fut.set_result(event.value.strip())

    def action_abort(self) -> None:
        """Ctrl+C: show a visible ``Game aborted.`` banner, then exit.

        Kept uniform across in-game and post-END states; if ``_game_over``
        is already True the banner is mildly redundant but harmless, and
        avoids a second code path that could diverge.
        """
        try:
            log = self.query_one("#public-log", RichLog)
            log.write(Text.from_markup("[bold red]Game aborted.[/]"))
        except Exception:  # noqa: BLE001
            # Widget might not be mounted yet (ctrl+c pressed before mount
            # completes). Fall through to exit regardless.
            pass
        # Wake any parked resume future so the driver worker unwinds even
        # if it was awaiting human input when Ctrl+C arrived. Mirrors the
        # _on_quit_decision cancellation; without it, a Ctrl+C during a
        # PointingModal / VoteModal would leave the worker hanging until
        # the daemon-Timer fallback fires.
        pending = self._pending_resume
        if pending is not None and not pending.done():
            pending.cancel()
        self.exit()
        # Fallback: same rationale as _on_quit_decision — guards against
        # a producer thread parked inside an in-flight Bedrock call.
        self._arm_hard_exit_fallback()

    def action_request_quit(self) -> None:
        """Esc: push the :class:`QuitModal` to confirm before exiting.

        Guards against double-stacking when a modal (e.g. ``QuitModal``
        itself, ``FailureModal``, ``PointingModal``, ``VoteModal``) is
        already on screen — those modals own their own Esc handling.
        Raising :class:`SkipAction` (instead of returning) yields the
        keystroke back to Textual's binding chain so the modal's own
        ``escape`` binding can dismiss it; a bare ``return`` is treated
        by the dispatcher as "handled" and would swallow the key.
        """
        from textual.actions import SkipAction

        if isinstance(self.screen, ModalScreen):
            raise SkipAction()
        self.push_screen(QuitModal(), self._on_quit_decision)

    def action_leave(self) -> None:
        """``q``: leave a FINISHED game, and only a finished game.

        Spec 040 (The Moderator's Closing Story), Slice 6 — the exit
        contract. It replaces :meth:`on_key`, which exited on *any* key once
        the game was over and so made the ending unreadable: measured, a
        ``pageup`` at game-over exits the app instead of scrolling, with
        ``is_running`` False straight after the press.

        **The leaving key has to be named positively.** The obvious
        alternative — keep ``on_key`` and let the scroll keys through — is
        not expressible, because ``App.on_key`` still fires for keys the
        focused widget's own binding already consumed. Measured on Textual
        8.2.4: with ``#public-log`` focused, ``pageup`` moved ``scroll_y``
        114 -> 104 **and** ``App.on_key`` saw ``'pageup'``. There is no
        "was this handled?" flag on the event to test.

        ``on_key`` is **deleted** rather than narrowed, so there is exactly
        one dispatch path for one intent; two would resolve in whatever
        order the installed Textual version happens to use.

        Three keys leave, and the other two were already advertised, so this
        adds a key rather than replacing a contract: ``q`` (here), ``escape``
        (:meth:`action_request_quit` — via the confirmation modal) and
        ``ctrl+c`` (:meth:`action_abort`). ``enter`` is deliberately **not**
        one of them: it is the submit key for every Day turn, so the
        player's last act before the ending is an ``enter``, and a stray
        second one would throw the ending away.

        Two guards, and both earn their place:

        - ``ModalScreen`` -> :class:`SkipAction`, mirroring
          :meth:`action_request_quit`. Without it ``q`` fires from *under*
          :class:`~graphia.ui.failure_modal.FailureModal`, whose whole job
          is to hand the player the CloudWatch coordinates for a remote
          crash — the only pointer they have to the server-side log.
        - ``not _game_over`` -> :class:`SkipAction`, so ``q`` mid-game is
          given back to the binding chain instead of being swallowed.
          :class:`SkipAction` rather than a bare ``return`` for the reason
          :meth:`action_request_quit` records: the dispatcher reads a
          ``return`` as "handled".
        """
        from textual.actions import SkipAction

        if isinstance(self.screen, ModalScreen):
            raise SkipAction()
        if not self._game_over:
            raise SkipAction()
        self.exit()

    def _arm_hard_exit_fallback(self, delay: float = 0.5) -> None:
        """Arm a daemon ``threading.Timer`` that ``os._exit(0)``s after ``delay`` seconds.

        Defensive guard against a producer thread still parked inside a
        Bedrock LLM call when the user requests quit. ``asyncio.to_thread``
        uses the default ``ThreadPoolExecutor`` whose workers are non-daemon,
        so interpreter exit blocks until they finish — which can be the
        full 1–10s of the in-flight model invocation. The driver's cancel-
        without-await pattern handles the asyncio side, but the underlying
        thread cannot be cancelled from Python.

        If Textual + the cleaned-up driver shut down within ``delay`` (the
        common case), ``os._exit`` never fires because the interpreter is
        already gone. If something still hangs, the user gets a clean
        process death within half a second.

        Skipped when ``PYTEST_CURRENT_TEST`` is set so the test suite is
        never killed mid-run by a stray fallback Timer.
        """
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return
        timer = threading.Timer(delay, lambda: os._exit(0))
        timer.daemon = True
        timer.start()

    async def _on_quit_decision(self, confirm: bool | None) -> None:
        """Dismiss callback for :class:`QuitModal` — exit only on ``True``.

        ``False`` (player picked **No** / pressed ``n`` / pressed Esc) and
        ``None`` (modal dismissed without a value) both fall through so
        the player stays in the running game.

        On confirm we must do two things, in order, to avoid a hung
        process in a real terminal (test-mode pilots tick synchronously
        and never hit this hang, which is why the suite stays green):

        1. **Wake the driver worker.** ``_drive`` is almost always
           parked on ``await self._pending_resume`` inside
           ``_prompt_via_input`` / a ``push_screen_wait`` modal. Cancelling
           that future raises :class:`asyncio.CancelledError` —
           a :class:`BaseException`, not a :class:`Exception`, so the
           worker's broad ``except Exception`` handler does **not**
           swallow it. The cancellation propagates out, the worker
           unwinds, and Textual is free to shut down.
        2. **Defer ``exit()`` until after the modal teardown tick.**
           Calling ``self.exit()`` synchronously from the dismiss
           callback races the modal's own ``_pop_screen`` and leaves the
           screen stack half-torn-down — the UI disappears but the
           process never reaches a clean shutdown. ``call_after_refresh``
           schedules ``exit`` after the next refresh, by which point the
           modal is fully popped and the worker cancellation above has
           had a chance to propagate.
        """
        if not confirm:
            return
        # Record an abandoned game only if one is genuinely in progress: it has
        # started (human_id observed from the graph stream), is not already at
        # END (_game_over), and has not already been folded into the career.
        # Esc on the end screen, or a second Esc after a normal end, records
        # nothing. Best-effort under a short timeout so a slow remote Memory
        # write can never outlive the hard-exit fallback armed below — if it
        # can't finish in time we silently drop it (same net result as Ctrl+C).
        if (
            not self._game_over
            and not self._career_recorded
            and self._human_id is not None
            and self._stats_store is not None
        ):
            self._career_recorded = True
            if self._career_emitter is not None and self._thread_id is not None:
                # Fire-and-forget: the quit flow must never raise on a remote
                # emit failure. NoOp in local mode.
                try:
                    self._career_emitter.emit(
                        self._thread_id,
                        CareerEvent(
                            kind="game_abandoned",
                            session_id=self._thread_id,
                            human_role=self._latest_state.get("human_role", ""),
                            rounds_so_far=int(self._latest_state.get("cycle", 0)),
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass
            summary = summarize(self._latest_state, self._human_id, "abandoned")
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._stats_store.record, summary),
                    timeout=0.4,
                )
            except Exception:  # noqa: BLE001
                pass
        pending = self._pending_resume
        if pending is not None and not pending.done():
            pending.cancel()
        self.call_after_refresh(self.exit)
        # Fallback: if the producer thread is parked inside a Bedrock LLM
        # call, the asyncio cancel-without-await in driver._consume_stream
        # cannot stop the underlying non-daemon worker, and the interpreter
        # would block waiting for it. The daemon Timer guarantees process
        # death within ~0.5s regardless. See _arm_hard_exit_fallback.
        self._arm_hard_exit_fallback()

    def _enter_endgame(self, log: RichLog) -> None:
        """Put ``#public-log`` into its post-game reading state.

        Spec 040 (The Moderator's Closing Story), Slice 6. **The one place
        every ending path funnels through**, so no ending variant can end up
        with a different contract than another: the normal win/draw ending,
        the Day-cap runaway ending (same code path — it just has no career
        panel, because ``_OUTCOME_BY_WINNER`` deliberately omits
        ``"runaway"``), and :meth:`_drive`'s ``except``.

        **Focus is the load-bearing line, and it is not decoration.** By the
        time a game ends the docked ``Input`` has been disabled again by
        :meth:`_prompt_via_input`'s ``finally``, and Textual then moves focus
        to the next focusable widget **in DOM order** — which is
        ``#private-log``, the six-row whispers pane, not the pane carrying the
        ending. Measured on Textual 8.2.4 with this app's real ``compose()``:
        ``screen.focus_chain`` is ``[#private-log, #public-log,
        #player-input]``, and disabling the focused ``Input`` lands focus on
        ``#private-log``. ``CornerBadge.can_focus`` is ``False`` and the
        header is a ``Static``, so neither is a candidate.

        Without this call PgUp scrolls the whispers pane while the ending
        stays pinned, and **nothing visibly happens** — every scrolling
        criterion in this slice would pass its code path and fail the person.

        **Auto-scroll is then switched off, and ``max_lines`` is left
        alone.** ``RichLog.auto_scroll`` defaults to ``True`` (probed), which
        is right for the whole game and wrong the instant the person starts
        reading back: any later write jumps the view to the bottom, yanking
        them out of the roster mid-sentence. Measured on this pane —
        ``scroll_y`` 91 -> 107 on one write. ``max_lines`` stays unset on
        purpose: capping it would discard the oldest rows, and the oldest
        rows of an ending are the winner line and the roster reveal, i.e.
        exactly what a cap would push out of reach.

        **The affordance is pinned to the frame, not written into the log.**
        ``#public-log`` already carries a ``round`` border, so setting
        ``border_subtitle`` paints :data:`EXIT_HINT` on it — where it survives
        every scroll, unlike the banner row, which leaves the viewport the
        moment the person scrolls up. Measured to add **zero** rows to
        ``RichLog.lines``. No ``Footer`` is added: there is none in this app
        today, so ``show=True`` on a binding renders nowhere, and docking one
        would cost a permanent row for the whole game to advertise a key that
        only matters on the last screen.

        Nothing else is needed for the scrolling itself. ``RichLog`` is a
        ``ScrollView`` with ``overflow-y: scroll`` and ``can_focus = True``;
        probed at 120 rows in an 11-row viewport it reported
        ``max_scroll_y=114`` and responded to ``up`` / ``pageup`` / ``home``
        / ``end``.

        Called **after** the final banner write rather than at the moment
        ``_game_over`` flips, so the pane freezes at the true bottom without
        depending on anything arriving later. **The stronger claim that the
        earlier placement would hide the banner is NOT true, and was measured
        rather than assumed**: freezing between the career panel and the
        banner still leaves the banner on the last visible row, because
        ``RichLog.write``'s ``scroll_end(immediate=False)`` defers to after
        the next refresh and re-reads ``max_scroll_y`` *then* — by which time
        the banner is in ``lines``. The ordering here is the one that does not
        rely on that.

        One measured trap for anyone testing this, because it looks like a
        bug in this method: ``RichLog`` defers **all** writes until its first
        ``Resize`` (``_size_known``), and a deferred write replays through
        ``write()`` again, re-reading ``auto_scroll`` at *replay* time. So if
        a whole game is driven before the pane has ever been sized, every row
        replays with auto-scroll already off and the pane sits at row 0 with
        the ending below the fold. Real play cannot hit it (hundreds of
        refreshes precede the ending); a pilot test that never pauses can.
        """
        log.auto_scroll = False
        log.border_subtitle = f" {EXIT_HINT} "
        log.focus()

    async def _drive(self) -> None:
        log = self.query_one("#public-log", RichLog)
        try:
            store = self._stats_store or make_stats_store(self.config)
            self._stats_store = store
            emitter = self._career_emitter or make_career_emitter(self.config)
            self._career_emitter = emitter
            # Written directly to the public pane (not as a graph message) so
            # it bypasses the `private_to` filter and shows before gameplay.
            log.write(Text(render_greeting(store.load())))
            graph, thread_id = build_graph(self.config, career_emitter=emitter)
            self._thread_id = thread_id
            run_config = make_run_config(thread_id)
            self._graph = graph
            self._run_config = run_config
            await drive_graph(
                graph=graph,
                run_config=run_config,
                initial={"messages": []},
                logger=self.logger,
                on_message=self._handle_graph_message,
                request_resume=self._request_resume,
                config=self.config,
                on_state=self._on_graph_state,
            )
            self._record_career(log)
            self._game_over = True
            log.write(
                Text.from_markup(f"[bold green]Game over.[/] {EXIT_HINT}")
            )
            self._enter_endgame(log)
        except Exception as exc:  # noqa: BLE001
            self.logger.record(
                {"error": repr(exc), "traceback": traceback.format_exc()}
            )
            self._game_over = True
            if self.config.remote_mode:
                # Remote crash: the graph ran in the deployed Runtime, so the
                # local JSONL log only has the client-side stack. Hand the
                # player the CloudWatch coordinates (log group + per-session
                # `{ $.thread_id = ... }` filter) via a self-contained modal.
                # Keep a one-line banner too so the screen behind the modal
                # still reads as an error state after dismissal.
                log.write(
                    Text.from_markup(
                        f"[bold red]Remote game error.[/] {EXIT_HINT}"
                    )
                )
                # Focus the ending pane BEFORE the modal goes up, so
                # dismissing the modal hands focus back to `#public-log`
                # rather than to the whispers pane.
                self._enter_endgame(log)
                self._show_failure_modal(exc)
            else:
                # Local mode: banner + JSONL log-file pointer.
                #
                # STYLED SPANS, not markup — the fault Task 2.1 fixed for
                # message bodies, at the one site it deliberately left for
                # this task rather than colliding with it. The path comes
                # from ``GRAPHIA_LOG_FILE``, so it is neither model nor
                # player content, which is why Task 2.1 scoped it out; but it
                # is still a value this call site does not author, and Rich
                # eats a lowercase bracket segment in it silently
                # (``/tmp/[run]/graphia.jsonl`` renders as ``/tmp//graphia.jsonl``)
                # or raises ``MarkupError`` on a ``[/...]`` one — inside the
                # handler whose entire job is to tell the player where the log
                # is. ``Text.assemble`` styles a character range instead of
                # parsing the text, so the path renders exactly as configured.
                log.write(
                    Text.assemble(
                        (f"Error — see {self.config.log_file}", "bold red"),
                        (f" {EXIT_HINT}", ""),
                    )
                )
                self._enter_endgame(log)

    _OUTCOME_BY_WINNER: dict[str, str] = {
        "law_abiding": "law_abiding_win",
        "mafia": "mafia_win",
        "draw": "draw",
    }

    def _record_career(self, log: RichLog) -> None:
        """Fold the just-finished game into the career and show the panel.

        Runs only on a real end (a ``winner`` is present in the streamed-state
        mirror); a graph that unwound without resolving has no outcome to
        record. The panel is written directly to the public pane — like the
        launch greeting — so it bypasses the ``private_to`` filter and lands
        just before the "Game over." banner.
        """
        if self._career_recorded:
            return
        winner = self._latest_state.get("winner")
        outcome = self._OUTCOME_BY_WINNER.get(winner) if isinstance(winner, str) else None
        if outcome is None or self._human_id is None or self._stats_store is None:
            return
        summary = summarize(self._latest_state, self._human_id, outcome)
        new = self._stats_store.record(summary)
        self._career_recorded = True
        log.write(Text(render_panel(new, summary)))

    def _show_failure_modal(self, exc: BaseException) -> None:
        """Push the remote-mode :class:`FailureModal` for an unhandled crash.

        The thread id may be ``None`` if the crash happened before
        ``build_graph`` returned; fall back to a placeholder so the filter
        expression is still well-formed and the modal never shows "None".
        Wrapped defensively — if the modal itself cannot be pushed (e.g. the
        screen stack is already torn down) the banner above still stands.
        """
        thread_id = self._thread_id or "<unknown>"
        try:
            self.push_screen(
                FailureModal(
                    thread_id=thread_id,
                    log_group=self.config.cloudwatch_log_group,
                    error_summary=f"{type(exc).__name__}: {exc}",
                )
            )
        except Exception:  # noqa: BLE001
            self.logger.record({"failure_modal_error": True})
