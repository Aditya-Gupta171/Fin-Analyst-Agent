"""The citation guardrail: every figure an agent writes must be a reference to a verified value.

This is what makes "the LLM never produces a number" actually hold, so it is tested directly against the
public functions rather than only indirectly through the agent graph.
"""

import pytest

from app.analysis.evidence import EvidenceIndex, check_prose, figures_in
from app.analysis.report import EvidenceItem
from app.domain.enums import Unit
from app.engine.references import bare_figures, render


def idx(**items: tuple[str, Unit, str]) -> EvidenceIndex:
    """A bare EvidenceIndex over hand-picked evidence, without needing a full EngineResult and Catalog."""
    index = object.__new__(EvidenceIndex)
    index.items = {
        ref: EvidenceItem(ref=ref, label=label, display=display, unit=unit, period="FY25")
        for ref, (label, unit, display) in items.items()
    }
    index.registry = {ref: item.display for ref, item in index.items.items()}
    index._by_figure = None
    return index


# ── bare_figures / contains_bare_figures ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Receivables grew 55%.",
        "DSO stretched to 98 days.",
        "Coverage fell to 1.8x.",
        "Operating cash flow was ₹50 crore.",
        "Margins contracted by 4.2%.",
        "The ratio is 0.30x now.",
    ],
)
def test_bare_figures_detects_unfenced_numbers(text: str) -> None:
    assert bare_figures(text)


@pytest.mark.parametrize(
    "text",
    [
        "Receivables grew {{m:receivables_growth@FY25}}.",
        "Disclosed under Regulation 33 of SEBI LODR for Q3FY25.",
        "The company reports for FY25 and FY24.",
        "Section 32 of the Companies Act applies.",
    ],
)
def test_bare_figures_ignores_citations_and_non_figures(text: str) -> None:
    assert not bare_figures(text)


def test_bare_figures_does_not_count_a_reference_body_as_a_figure() -> None:
    # the digits inside {{...@FY25}} must not themselves be flagged
    assert not bare_figures("As shown, {{m:dso@FY25}} is elevated.")


# ── render ────────────────────────────────────────────────────────────────────────────────────────────────


def test_render_substitutes_known_references() -> None:
    result = render("DSO is {{m:dso@FY25}}.", {"m:dso@FY25": "98 days"})
    assert result.text == "DSO is 98 days."
    assert result.cited == ("m:dso@FY25",)
    assert result.valid


def test_render_leaves_unknown_references_untouched_and_reports_them() -> None:
    result = render("Value is {{m:missing@FY25}}.", {})
    assert result.text == "Value is {{m:missing@FY25}}."
    assert result.unknown == ("m:missing@FY25",)
    assert not result.valid


def test_render_does_not_duplicate_a_unit_already_present_in_the_display() -> None:
    result = render("DSO reached {{m:dso@FY25}} days.", {"m:dso@FY25": "98 days"})
    assert result.text == "DSO reached 98 days."


def test_render_appends_a_distinct_unit_when_the_display_lacks_one() -> None:
    result = render("Score is {{m:score@FY25}}%.", {"m:score@FY25": "97"})
    assert result.text == "Score is 97%."


# ── check_prose ───────────────────────────────────────────────────────────────────────────────────────────


def test_check_prose_accepts_text_built_only_from_citations() -> None:
    index = idx(**{"m:dso@FY25": ("DSO", Unit.DAYS, "98 days")})
    assert check_prose("DSO is {{m:dso@FY25}}, which is high.", index, set(), "analysis") == []


def test_check_prose_rejects_a_figure_written_out_by_hand() -> None:
    index = idx(**{"m:dso@FY25": ("DSO", Unit.DAYS, "98 days")})
    problems = check_prose("DSO is 98 days, which is high.", index, set(), "analysis")
    assert any("98 days" in p and "cite it" in p for p in problems)


def test_check_prose_rejects_an_unknown_reference() -> None:
    index = idx()
    problems = check_prose("See {{m:nope@FY25}}.", index, set(), "analysis")
    assert any("unknown reference" in p for p in problems)


def test_check_prose_allows_an_explicitly_permitted_figure() -> None:
    # e.g. a regulatory threshold quoted from the knowledge base, not a value being asserted about the company
    index = idx()
    problems = check_prose("The ICDR cap is 25%.", index, {"25%"}, "analysis")
    assert problems == []


def test_check_prose_rejects_knowledge_style_braces_used_as_a_citation() -> None:
    index = idx(**{"m:dso@FY25": ("DSO", Unit.DAYS, "98 days")})
    problems = check_prose("See {{working-capital/receivables}} for context.", index, set(), "analysis")
    assert any("not an evidence reference" in p for p in problems)


def test_check_prose_field_name_appears_in_the_problem() -> None:
    index = idx()
    problems = check_prose("Grew 12%.", index, set(), "headline")
    assert all(p.startswith("headline:") for p in problems)


# ── link_figures ──────────────────────────────────────────────────────────────────────────────────────────


def test_link_figures_rewrites_an_unambiguous_bare_figure_to_its_reference() -> None:
    index = idx(**{"m:dso@FY25": ("DSO", Unit.DAYS, "98 days")})
    linked = index.link_figures("DSO is 98 days now.", set())
    assert linked == "DSO is {{m:dso@FY25}} now."
    assert check_prose(linked, index, set(), "analysis") == []


def test_link_figures_leaves_an_ambiguous_figure_for_the_guardrail_to_reject() -> None:
    index = idx(
        **{
            "m:a@FY25": ("A", Unit.RATIO, "55.0%"),
            "m:b@FY25": ("B", Unit.RATIO, "55.0%"),
        }
    )
    linked = index.link_figures("Growth of 55.0% was seen.", set())
    assert linked == "Growth of 55.0% was seen."  # not rewritten: two references share this value
    assert check_prose(linked, index, set(), "analysis")  # still flagged as an uncited figure


def test_link_figures_prefers_a_reference_from_the_preferred_set_when_ambiguous() -> None:
    index = idx(
        **{
            "m:a@FY25": ("A", Unit.RATIO, "55.0%"),
            "m:b@FY25": ("B", Unit.RATIO, "55.0%"),
        }
    )
    linked = index.link_figures("Growth of 55.0% was seen.", set(), preferred=["m:b@FY25"])
    assert linked == "Growth of {{m:b@FY25}} was seen."


def test_link_figures_does_not_touch_figures_in_the_skip_set() -> None:
    index = idx(**{"m:dso@FY25": ("DSO", Unit.DAYS, "98 days")})
    linked = index.link_figures("The regulatory cap is 25%.", {"25%"})
    assert linked == "The regulatory cap is 25%."


def test_link_figures_handles_negative_amounts_correctly() -> None:
    index = idx(**{"m:fcf@FY25": ("Free cash flow", Unit.INR_CRORE, "-₹80.00 cr")})
    linked = index.link_figures("Free cash flow turned negative at -₹80 cr.", set())
    assert linked == "Free cash flow turned negative at {{m:fcf@FY25}}."


def test_link_figures_does_not_confuse_positive_and_negative_values() -> None:
    index = idx(**{"m:fcf@FY25": ("Free cash flow", Unit.INR_CRORE, "₹80.00 cr")})
    linked = index.link_figures("Free cash flow turned negative at -₹80 cr.", set())
    assert linked == "Free cash flow turned negative at -₹80 cr."  # sign mismatch: not linked


# ── figures_in ────────────────────────────────────────────────────────────────────────────────────────────


def test_figures_in_collects_normalised_figures_across_texts() -> None:
    figures = figures_in(["The cap is 25%.", "Threshold: 25 %."])
    assert figures == {"25%"}


def test_figures_in_empty_for_texts_with_no_figures() -> None:
    assert figures_in(["No numbers here.", "Nor here."]) == set()
