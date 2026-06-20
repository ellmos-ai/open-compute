"""Anthropic Claude computer-use backend.

Uses the Anthropic Messages API with the ``computer`` tool. The model returns
``tool_use`` blocks; the host (the agent loop here) executes them and returns the
resulting screenshot. This backend translates Claude's tool_use actions into the
package's canonical :class:`~open_compute.actions.Action` objects.

Verified API surface (Anthropic docs, computer-use):
    - tool type:   ``computer_20251124`` (newer) / ``computer_20250124`` (older)
    - tool name:   ``computer``
    - beta header: ``computer-use-2025-11-24`` (or ``computer-use-2025-01-24``)
    - parameters:  ``display_width_px``, ``display_height_px``, ``display_number``
    - coordinates: global screen pixels; (0,0) = top-left of the screen.

The ``anthropic`` SDK is imported **lazily** inside :meth:`__init__` so that
importing this module never requires the SDK. Install it via the optional
extra: ``pip install open-compute[claude]``.
"""

from __future__ import annotations

from typing import Any

from ..actions import Action, ActionType
from ..perception import Observation
from .base import BackendResult

# Default model and the computer-tool / beta-header pair. These are the
# documented current-generation values; override via the constructor if needed.
DEFAULT_MODEL = "claude-opus-4-8"
COMPUTER_TOOL_TYPE = "computer_20251124"
COMPUTER_USE_BETA = "computer-use-2025-11-24"


class ClaudeComputerBackend:
    """Claude Messages API + ``computer`` tool backend.

    Args:
        width, height: ``display_width_px`` / ``display_height_px`` advertised to
            the model and used to normalize the pixel coordinates it returns.
        model: Claude model id. Defaults to :data:`DEFAULT_MODEL`.
        api_key: Optional explicit key; otherwise the SDK reads the environment.
        tool_type: Computer tool type; defaults to :data:`COMPUTER_TOOL_TYPE`.
        beta_header: ``anthropic-beta`` value; defaults to :data:`COMPUTER_USE_BETA`.
        max_tokens: Response token cap for each turn.
        client: Optional pre-built Anthropic client (for dependency injection in
            tests); when provided the SDK is not imported here.

    Raises:
        ImportError: If the ``anthropic`` SDK is not installed and no ``client``
            was injected.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        tool_type: str = COMPUTER_TOOL_TYPE,
        beta_header: str = COMPUTER_USE_BETA,
        max_tokens: int = 4096,
        client: Any | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.model = model
        self.tool_type = tool_type
        self.beta_header = beta_header
        self.max_tokens = max_tokens
        self._messages: list[dict[str, Any]] = []
        self._last_tool_use_ids: list[str] = []

        if client is not None:
            self._client = client
        else:
            try:
                import anthropic  # noqa: F401  (lazy, optional dependency)
            except ImportError as exc:  # pragma: no cover - exercised without SDK
                raise ImportError(
                    "The Claude backend requires the 'anthropic' package. "
                    "Install it with: pip install open-compute[claude]"
                ) from exc
            self._client = anthropic.Anthropic(api_key=api_key)

    @property
    def name(self) -> str:
        return "claude"

    @property
    def tools(self) -> list[dict[str, Any]]:
        """The ``computer`` tool definition sent on every request."""
        return [
            {
                "type": self.tool_type,
                "name": "computer",
                "display_width_px": self.width,
                "display_height_px": self.height,
                "display_number": 1,
            }
        ]

    def start(self, goal: str, observation: Observation) -> BackendResult:
        self._messages = [{"role": "user", "content": goal}]
        return self._turn()

    def step(self, observation: Observation) -> BackendResult:
        # Feed the screenshot(s) back as tool_result(s) for the prior tool_use(s).
        if self._last_tool_use_ids:
            results = [
                self._tool_result_block(tid, observation)
                for tid in self._last_tool_use_ids
            ]
            self._messages.append({"role": "user", "content": results})
        return self._turn()

    def _turn(self) -> BackendResult:
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            tools=self.tools,
            betas=[self.beta_header],
            messages=self._messages,
        )
        # Echo the assistant turn back into history (preserves tool_use blocks).
        self._messages.append({"role": "assistant", "content": response.content})

        actions: list[Action] = []
        self._last_tool_use_ids = []
        text_parts: list[str] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                self._last_tool_use_ids.append(block.id)
                actions.append(self._from_tool_use(block.input))

        done = getattr(response, "stop_reason", None) != "tool_use"
        return BackendResult(
            actions=actions,
            done=done,
            message="\n".join(p for p in text_parts if p) or None,
            raw=response,
        )

    def _tool_result_block(self, tool_use_id: str, observation: Observation) -> dict[str, Any]:
        """Build a ``tool_result`` block carrying the latest screenshot."""
        import base64

        content: list[dict[str, Any]] = []
        if observation.screenshot is not None:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(observation.screenshot).decode("ascii"),
                    },
                }
            )
        else:
            content.append({"type": "text", "text": "screenshot unavailable"})
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

    def _from_tool_use(self, payload: dict[str, Any]) -> Action:
        """Translate a Claude computer-tool action dict into a canonical Action."""
        from ..coordinates import normalize

        name = payload.get("action", "screenshot")
        coord = payload.get("coordinate")
        start = payload.get("start_coordinate")

        nx = ny = None
        if coord:
            nx, ny = normalize(coord[0], coord[1], self.width, self.height)

        kwargs: dict[str, Any] = {}
        if name == "left_click_drag" and start:
            sx, sy = normalize(start[0], start[1], self.width, self.height)
            kwargs.update(x=sx, y=sy, end_x=nx, end_y=ny)
        elif nx is not None:
            kwargs.update(x=nx, y=ny)

        if name in ("type", "key"):
            kwargs["text"] = payload.get("text")
        if name == "scroll":
            kwargs["scroll_direction"] = payload.get("scroll_direction")
            kwargs["scroll_amount"] = payload.get("scroll_amount")
        if name == "wait":
            kwargs["duration"] = payload.get("duration")

        return Action(ActionType(name), **kwargs)
