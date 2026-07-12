"""Permission / safety gate.

A central policy layer evaluated before *every* action. It implements a
"confirm before risky actions" policy by default and supports allow/deny rules,
a confirmation callback (human-in-the-loop), and an audit log. This mirrors the
confirmations-policy concept from the analysis report and is fully testable
without any backend.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .actions import Action, ActionType


class Decision(str, Enum):
    """Outcome of a policy evaluation for one action."""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


# Actions considered "risky" by default: they change external state or are hard
# to reverse. Read-only/observational actions (screenshot, cursor_position,
# wait, mouse_move) are allowed without confirmation.
#
# The hold primitives are risky in both halves: ``mouse_down``/``key_down``
# obviously so, but the matching ``_up`` is gated too, because a policy that
# denied the press must not be talked into a stray release, and ``read_only``
# must stay free of synthesized input entirely. A gate that allowed the press
# and blocked the release would strand the host with a button held down — which
# is why an executor that presses is also expected to offer ``release_all()``.
DEFAULT_RISKY_ACTIONS = frozenset(
    {
        ActionType.LEFT_CLICK,
        ActionType.RIGHT_CLICK,
        ActionType.MIDDLE_CLICK,
        ActionType.DOUBLE_CLICK,
        ActionType.TRIPLE_CLICK,
        ActionType.LEFT_CLICK_DRAG,
        ActionType.TYPE,
        ActionType.KEY,
        ActionType.LAUNCH_APP,
        ActionType.MOUSE_DOWN,
        ActionType.MOUSE_UP,
        ActionType.KEY_DOWN,
        ActionType.KEY_UP,
    }
)


@dataclass
class PolicyResult:
    """The decision plus a human-readable reason and the evaluated action."""

    decision: Decision
    reason: str
    action: Action


# A confirmation callback receives the pending action and returns True to allow.
ConfirmCallback = Callable[[Action], bool]


@dataclass
class SafetyPolicy:
    """Configurable permission gate.

    Args:
        mode: ``confirm`` (default) asks for confirmation on risky actions;
            ``allow_all`` permits everything (useful for fully sandboxed VMs);
            ``read_only`` denies any state-changing action.
        risky_actions: Action types that require confirmation in ``confirm``
            mode. Defaults to :data:`DEFAULT_RISKY_ACTIONS`.
        denied_actions: Action types that are always denied.
        confirm_callback: Optional human-in-the-loop callback. When ``None`` and
            a confirmation is required, :meth:`evaluate` returns a ``CONFIRM``
            decision and the caller decides; when provided, the callback's
            boolean result is folded into an ``ALLOW``/``DENY``.
    """

    mode: str = "confirm"
    risky_actions: frozenset[ActionType] = DEFAULT_RISKY_ACTIONS
    denied_actions: frozenset[ActionType] = frozenset()
    confirm_callback: ConfirmCallback | None = None
    audit_log: list[PolicyResult] = field(default_factory=list)

    _VALID_MODES = ("confirm", "allow_all", "read_only")

    def __post_init__(self) -> None:
        if self.mode not in self._VALID_MODES:
            raise ValueError(
                f"mode must be one of {self._VALID_MODES}, got {self.mode!r}"
            )

    def evaluate(self, action: Action) -> PolicyResult:
        """Evaluate one action against the policy and record it in the audit log."""
        result = self._evaluate(action)
        self.audit_log.append(result)
        return result

    def _evaluate(self, action: Action) -> PolicyResult:
        t = action.type
        if t in self.denied_actions:
            return PolicyResult(Decision.DENY, f"{t.value} is on the deny list", action)

        if self.mode == "read_only":
            if t in self.risky_actions:
                return PolicyResult(
                    Decision.DENY,
                    f"{t.value} is a state-changing action; policy is read_only",
                    action,
                )
            return PolicyResult(Decision.ALLOW, "read-only action", action)

        if self.mode == "allow_all":
            return PolicyResult(Decision.ALLOW, "allow_all mode", action)

        # confirm mode
        if t not in self.risky_actions:
            return PolicyResult(Decision.ALLOW, "non-risky action", action)

        if self.confirm_callback is None:
            return PolicyResult(
                Decision.CONFIRM,
                f"{t.value} requires confirmation",
                action,
            )

        approved = bool(self.confirm_callback(action))
        if approved:
            return PolicyResult(Decision.ALLOW, "confirmed by callback", action)
        return PolicyResult(Decision.DENY, "rejected by callback", action)

    def is_allowed(self, action: Action) -> bool:
        """Convenience: True only if the action is outright allowed (no confirm)."""
        return self.evaluate(action).decision is Decision.ALLOW
