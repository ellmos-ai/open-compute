# tests/test_clirec_format.py
import pytest
from open_compute.clirec import format as fmt


def _sample() -> fmt.Recording:
    return fmt.Recording(
        title="LinkedIn-Post",
        created="2026-06-28T14:03:11",
        host="LAPTOP",
        resolution="2560x1440",
        goal="Einen Beitrag posten.",
        params=[{"name": "post_text", "desc": "Der Text", "default": ""}],
        steps=[
            fmt.Step(index=1, t=0.0, action="click", x=1180, y=64, btn="left",
                     ui_name="Beitrag starten", ui_window="LinkedIn", ui_role="button",
                     frame="0001.png"),
            fmt.Step(index=2, t=1.42, action="type", text="${post_text}",
                     ui_name="Editor", ui_window="Beitrag", ui_role="edit"),
        ],
    )


def test_roundtrip_preserves_recording():
    rec = _sample()
    text = fmt.dumps(rec)
    back = fmt.loads(text)
    assert back.title == rec.title
    assert back.resolution == "2560x1440"
    assert len(back.steps) == 2
    assert back.steps[0].x == 1180 and back.steps[0].btn == "left"
    assert back.steps[0].ui_name == "Beitrag starten"
    assert back.steps[1].action == "type" and back.steps[1].text == "${post_text}"


def test_validate_flags_missing_steps_section():
    problems = fmt.validate("# clirec-version: 1\ntitle: x\n")
    assert any("steps" in p.lower() for p in problems)


def test_validate_ok_returns_empty():
    assert fmt.validate(fmt.dumps(_sample())) == []


def test_apply_params_substitutes_placeholder():
    rec = fmt.apply_params(_sample(), {"post_text": "Hallo Welt"})
    assert rec.steps[1].text == "Hallo Welt"
    # Original bleibt unangetastet (kein In-Place-Mutieren):
    original = _sample()
    fmt.apply_params(original, {"post_text": "Hallo Welt"})
    assert original.steps[1].text == "${post_text}"
