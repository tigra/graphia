"""Graphia Textual app: private Moderator panel alongside public chat."""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from graphia.config import GraphiaConfig, load_config
from graphia.driver import drive_graph
from graphia.graph import build_graph, make_run_config
from graphia.logging import StreamTraceLogger, setup_logger
from graphia.ui.badge import CornerBadge
from graphia.ui.widgets import PointingModal, VoteModal


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
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "abort", "Abort", show=False, priority=True),
    ]

    config: GraphiaConfig
    logger: StreamTraceLogger

    def __init__(self) -> None:
        super().__init__()
        # Loaded eagerly so `compose()` can read `remote_mode` for the badge
        # label. `on_mount` reuses the same instance instead of reloading.
        self.config = load_config()
        self._pending_resume: asyncio.Future[Any] | None = None
        self._human_id: str | None = None
        self._graph: CompiledStateGraph | None = None
        self._run_config: dict | None = None
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
        # announced or an unrecoverable error was caught). While False the
        # on_key handler is a no-op so normal in-game bindings (q, ctrl+c)
        # still work. Once True, any keypress exits.
        self._game_over: bool = False
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
        log = self.query_one("#private-log", RichLog)
        body = _content_to_text(msg.content)
        log.write(Text.from_markup(f"[bold magenta]Moderator (private):[/] {body}"))

    def _write_public(self, msg: BaseMessage) -> None:
        log = self.query_one("#public-log", RichLog)
        body = _content_to_text(msg.content)
        if isinstance(msg, SystemMessage):
            markup = f"[bold cyan]Moderator:[/] {body}"
        elif isinstance(msg, AIMessage):
            markup = f"[bold]{_speaker_of(msg)}:[/] {body}"
        else:
            return
        log.write(Text.from_markup(markup))

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
            log.write(
                Text.from_markup(f"[dim]It's your turn, {speaker_name}.[/dim]")
            )
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
            # push_screen_wait blocks this worker until dismiss() is called;
            # PointingModal resolves with the selected target's .id (str),
            # which is exactly what the graph interrupt expects as the
            # Command(resume=...) value.
            target_id = await self.push_screen_wait(PointingModal(options=options))
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
        self.exit()

    def on_key(self, event: Any) -> None:
        """Exit on any keypress once the game has ended.

        While ``_game_over`` is False this is a no-op, so the normal Textual
        bindings (q to quit, ctrl+c to quit) continue to work during play.
        Once the graph has reached END (or errored out), any key dismisses
        the final screen and cleanly exits back to the shell.
        """
        if self._game_over:
            self.exit()

    async def _drive(self) -> None:
        log = self.query_one("#public-log", RichLog)
        try:
            graph, thread_id = build_graph(self.config)
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
            self._game_over = True
            log.write(
                Text.from_markup("[bold green]Game over.[/] Press any key to exit.")
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.record(
                {"error": repr(exc), "traceback": traceback.format_exc()}
            )
            self._game_over = True
            log.write(
                Text.from_markup(
                    f"[bold red]Error — see {self.config.log_file}[/] "
                    "Press any key to exit."
                )
            )
