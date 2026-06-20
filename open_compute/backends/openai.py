"""OpenAI computer-use (CUA) backend.

Uses OpenAI's computer-use tool. The model emits ``computer_call`` actions with
pixel coordinates; the host executes them and returns a screenshot, mirroring
the Claude loop. This backend translates OpenAI actions into the package's
canonical :class:`~open_compute.actions.Action` objects.

[UNSICHER] Model name: the OpenAI computer-use surface and its model identifier
have shifted across previews (``computer-use-preview`` vs. newer ``gpt-5.x``
variants). The default below is ``computer-use-preview`` and is treated as
**configurable / not fully verified** -- override via the constructor. The exact
Responses-API request shape is also version-sensitive; this backend keeps the
SDK interaction minimal and is best validated against the live OpenAI docs
before production use.

The ``openai`` SDK is imported **lazily** inside :meth:`__init__`. Install it via
the optional extra: ``pip install open-compute[openai]``.
"""

from __future__ import annotations

from typing import Any

from ..actions import Action, ActionType
from ..perception import Observation
from .base import BackendResult

# [UNSICHER] -- configurable; treat as not-fully-verified (see module docstring).
DEFAULT_MODEL = "computer-use-preview"


class OpenAIComputerBackend:
    """OpenAI computer-use backend.

    Args:
        width, height: Display dimensions advertised to the tool and used to
            normalize the pixel coordinates the model returns.
        model: OpenAI model id. Defaults to :data:`DEFAULT_MODEL` ([UNSICHER]).
        api_key: Optional explicit key; otherwise the SDK reads the environment.
        client: Optional pre-built OpenAI client (dependency injection / tests).

    Raises:
        ImportError: If the ``openai`` SDK is not installed and no ``client``
            was injected.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.model = model
        self._goal: str | None = None
        self._last_response_id: str | None = None
        self._last_call_id: str | None = None

        if client is not None:
            self._client = client
        else:
            try:
                import openai  # noqa: F401  (lazy, optional dependency)
            except ImportError as exc:  # pragma: no cover - exercised without SDK
                raise ImportError(
                    "The OpenAI backend requires the 'openai' package. "
                    "Install it with: pip install open-compute[openai]"
                ) from exc
            self._client = openai.OpenAI(api_key=api_key)

    @property
    def name(self) -> str:
        return "openai"

    @property
    def tools(self) -> list[dict[str, Any]]:
        """The computer-use tool definition."""
        return [
            {
                "type": "computer_use_preview",
                "display_width": self.width,
                "display_height": self.height,
                "environment": "browser",
            }
        ]

    def start(self, goal: str, observation: Observation) -> BackendResult:
        self._goal = goal
        response = self._client.responses.create(
            model=self.model,
            tools=self.tools,
            input=goal,
            truncation="auto",
        )
        return self._parse(response)

    def step(self, observation: Observation) -> BackendResult:
        # Return the screenshot for the previous computer_call.
        import base64

        screenshot_b64 = (
            base64.b64encode(observation.screenshot).decode("ascii")
            if observation.screenshot is not None
            else ""
        )
        response = self._client.responses.create(
            model=self.model,
            tools=self.tools,
            previous_response_id=self._last_response_id,
            input=[
                {
                    "call_id": self._last_call_id,
                    "type": "computer_call_output",
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": f"data:image/png;base64,{screenshot_b64}",
                    },
                }
            ],
            truncation="auto",
        )
        return self._parse(response)

    def _parse(self, response: Any) -> BackendResult:
        self._last_response_id = getattr(response, "id", None)
        actions: list[Action] = []
        text_parts: list[str] = []
        self._last_call_id = None

        for item in getattr(response, "output", []) or []:
            itype = getattr(item, "type", None)
            if itype == "computer_call":
                self._last_call_id = getattr(item, "call_id", None)
                actions.append(self._from_action(getattr(item, "action", {})))
            elif itype == "message":
                text_parts.append(_extract_text(item))

        done = not actions
        return BackendResult(
            actions=actions,
            done=done,
            message="\n".join(p for p in text_parts if p) or None,
            raw=response,
        )

    def _from_action(self, action: Any) -> Action:
        """Translate an OpenAI computer action into a canonical Action."""
        from ..coordinates import normalize

        data = action if isinstance(action, dict) else _as_dict(action)
        atype = data.get("type", "screenshot")

        def npoint() -> tuple[float | None, float | None]:
            if "x" in data and "y" in data:
                return normalize(data["x"], data["y"], self.width, self.height)
            return None, None

        if atype == "screenshot":
            return Action(ActionType.SCREENSHOT)
        if atype == "wait":
            return Action(ActionType.WAIT, duration=1)
        if atype == "type":
            return Action(ActionType.TYPE, text=data.get("text", ""))
        if atype == "keypress":
            keys = data.get("keys", [])
            return Action(ActionType.KEY, text="+".join(keys))
        if atype == "move":
            x, y = npoint()
            return Action(ActionType.MOUSE_MOVE, x=x, y=y)
        if atype == "click":
            x, y = npoint()
            button = data.get("button", "left")
            mapping = {
                "left": ActionType.LEFT_CLICK,
                "right": ActionType.RIGHT_CLICK,
                "middle": ActionType.MIDDLE_CLICK,
            }
            return Action(mapping.get(button, ActionType.LEFT_CLICK), x=x, y=y)
        if atype == "double_click":
            x, y = npoint()
            return Action(ActionType.DOUBLE_CLICK, x=x, y=y)
        if atype == "scroll":
            x, y = npoint()
            sy = data.get("scroll_y", 0)
            sx = data.get("scroll_x", 0)
            direction = "down" if sy >= 0 else "up"
            if sx and not sy:
                direction = "right" if sx >= 0 else "left"
            return Action(
                ActionType.SCROLL,
                x=x if x is not None else 0.5,
                y=y if y is not None else 0.5,
                scroll_direction=direction,
                scroll_amount=abs(sy or sx) or 3,
            )
        if atype == "drag":
            path = data.get("path", [])
            if len(path) >= 2:
                sx, sy = normalize(path[0]["x"], path[0]["y"], self.width, self.height)
                ex, ey = normalize(path[-1]["x"], path[-1]["y"], self.width, self.height)
                return Action(ActionType.LEFT_CLICK_DRAG, x=sx, y=sy, end_x=ex, end_y=ey)
            return Action(ActionType.SCREENSHOT)

        return Action(ActionType.SCREENSHOT)


def _as_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def _extract_text(item: Any) -> str:
    content = getattr(item, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            text = getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else None)
            if text:
                parts.append(text)
        return " ".join(parts)
    return ""
