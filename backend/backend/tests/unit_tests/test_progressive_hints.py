from src.services.progressive_hints import (
    detect_framework,
    get_framework_sections,
    get_initial_hint,
)


def test_detect_framework_returns_direct_for_factual_marker():
    hint = "DIRECT: Just explain clearly and concisely what it is."
    assert detect_framework(hint) == "DIRECT"


def test_detect_framework_still_detects_star():
    hint = "Use STAR: describe the Situation, clarify your Task, detail the Actions you took."
    assert detect_framework(hint) == "STAR"


def test_detect_framework_still_detects_gcdio():
    hint = "Outline the Goal first, identify Constraints, explain your decision."
    assert detect_framework(hint) == "GCDIO"


def test_detect_framework_defaults_to_ctetd_when_no_signal():
    hint = "Start with context, explain the theory, give an example."
    assert detect_framework(hint) == "C-T-E-T-D"


def test_direct_framework_has_single_answer_section():
    assert get_framework_sections("DIRECT") == ["Answer"]


def test_direct_framework_initial_hint_shape():
    initial = get_initial_hint("DIRECT")
    assert initial["section_name"] == "Answer"
    assert initial["total_sections"] == 1
