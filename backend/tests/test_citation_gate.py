"""The FR-21 / FR-22 citation gate, with adversarial fixtures.

NFR-15 fails the build when citation precision drops below 100%, so this is the
suite that has to be paranoid. The cases that matter are the ones where a model
produces something that *looks* right: a plausible case name, a marker pointing
at a source that was never supplied, a quote that paraphrases rather than
quotes.
"""

from __future__ import annotations

import pytest

from app.ai.orchestration import (
    CitationGate,
    IndexedChunk,
    SentenceGate,
    split_sentences,
)


def _chunk(text: str, *, n: int = 1, page: int = 1) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c{n}",
        document_id="doc1",
        document_name="Master Services Agreement",
        document_type="contract",
        page_from=page,
        page_to=page,
        char_start=0,
        char_end=len(text),
        text=text,
        section_path=f"§ {n}.0",
        parent_key=f"p{n}",
    )


CAP = _chunk("Liability under this Agreement is capped at twelve months of fees.", n=1)
NOTICE = _chunk("Either party may terminate on sixty days' written notice.", n=2, page=4)


def _gate(*chunks: IndexedChunk, **over: object) -> CitationGate:
    return CitationGate(list(chunks), **over)  # type: ignore[arg-type]


# ----------------------------------------------- pass 1: resolution (FR-21)


def test_a_resolvable_citation_survives() -> None:
    result = _gate(CAP).check("Liability is capped at twelve months of fees.[[1]]")
    assert result.cited == [1]
    assert result.stripped == []
    assert "[[1]]" in result.text


def test_a_citation_naming_no_supplied_source_is_stripped() -> None:
    """A citation pointing nowhere is worse than none: it looks checkable."""
    result = _gate(CAP).check("The cap is twelve months.[[1]] See also.[[9]]")
    assert result.cited == [1]
    assert result.stripped == [9]
    assert "[[9]]" not in result.text
    assert "[[1]]" in result.text


def test_stripping_does_not_leave_a_space_before_punctuation() -> None:
    result = _gate(CAP).check("The cap applies [[7]].")
    assert "[[7]]" not in result.text
    assert " ." not in result.text


def test_zero_is_not_a_valid_source_number() -> None:
    """Sources are 1-indexed to match the prompt's `n` attribute; a 0 would
    index the last element in Python and cite the wrong clause."""
    result = _gate(CAP).check("Something.[[0]]")
    assert result.cited == []
    assert result.stripped == [0]


def test_a_repeated_citation_is_listed_once() -> None:
    result = _gate(CAP, NOTICE).check("A.[[1]] B.[[1]] C.[[2]]")
    assert result.cited == [1, 2]


# ------------------------------------------------ pass 2: authority (FR-22)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("See Smith v. Jones for the rule.", "case name"),
        ("The Court held so in 410 U.S. 113.", "reporter citation"),
        ("Jurisdiction rests on 28 U.S.C. § 1331.", "statute citation"),
        ("Dismissed under Fed. R. Civ. P. 12(b)(6).", "rule citation"),
        ("Filed as Case No. 21-cv-04567.", "docket number"),
    ],
)
def test_authority_absent_from_the_sources_is_caught(text: str, kind: str) -> None:
    """The single failure this product exists to prevent. A plausible
    fabrication is more dangerous than an obvious one."""
    findings = _gate(CAP).unsupported_authority(text)
    assert findings, f"{kind} went undetected: {text!r}"
    assert any(f.kind == kind for f in findings), [str(f) for f in findings]


def test_authority_that_is_in_the_sources_passes() -> None:
    """The gate forbids *fabrication*, not citation. Authority the attorney
    actually uploaded must not be flagged."""
    source = _chunk("This dispute is governed by 28 U.S.C. § 1331 per Smith v. Jones.")
    assert _gate(source).unsupported_authority("Jurisdiction rests on 28 U.S.C. § 1331.") == []
    assert _gate(source).unsupported_authority("See Smith v. Jones.") == []


def test_authority_matching_ignores_whitespace_differences() -> None:
    """Extraction re-wraps text, so a citation split across a line break in the
    source must still match the model's single-line rendering."""
    source = _chunk("as held in\n   410 U.S.    113 (1973)")
    assert _gate(source).unsupported_authority("See 410 U.S. 113.") == []


def test_fabricated_authority_forces_a_refusal() -> None:
    result = _gate(CAP).check("The cap is twelve months.[[1]] See Roe v. Wade, 410 U.S. 113.")
    assert result.must_refuse is True
    assert result.fabricated


def test_a_clean_answer_does_not_refuse() -> None:
    result = _gate(CAP).check("Liability is capped at twelve months of fees.[[1]]")
    assert result.must_refuse is False


def test_a_clause_number_is_not_mistaken_for_authority() -> None:
    """`§ 9.2` and `12.3` are the document's own structure, not citations.
    Flagging them would refuse every correct contract answer."""
    source = _chunk("Section 9.2 caps liability. Clause 12.3 governs notice.")
    assert _gate(source).unsupported_authority("Under § 9.2 the cap applies.") == []


# ------------------------------------------------ pass 3: groundedness


def test_a_supported_claim_scores_high() -> None:
    result = _gate(CAP).check("Liability is capped at twelve months of fees.[[1]]")
    assert result.groundedness[1] > 0.6


def test_an_unsupported_claim_scores_low() -> None:
    result = _gate(CAP).check("The agreement requires arbitration in Geneva.[[1]]")
    assert result.groundedness[1] < 0.3


def test_groundedness_reports_but_does_not_strip_by_default() -> None:
    """A proxy strict enough to catch real problems also strips correct
    citations that paraphrased. Off until M5 calibrates it."""
    result = _gate(CAP).check("The agreement requires arbitration in Geneva.[[1]]")
    assert result.cited == [1]
    assert "[[1]]" in result.text
    assert result.weakest_groundedness is not None


def test_groundedness_can_be_enforced_when_asked() -> None:
    gate = _gate(CAP, groundedness_floor=0.5, strip_ungrounded=True)
    result = gate.check("The agreement requires arbitration in Geneva.[[1]]")
    assert result.cited == []
    assert "[[1]]" not in result.text
    assert 1 in result.stripped


def test_an_uncited_assertion_is_counted() -> None:
    result = _gate(CAP).check(
        "Liability is capped at twelve months of fees.[[1]] "
        "The agreement also imposes a mutual confidentiality obligation on both parties."
    )
    assert result.uncited_claims == 1


def test_saying_the_documents_are_silent_is_not_an_uncited_claim() -> None:
    """There is nothing to cite for an absence, and FR-16's "not addressed" is
    a required answer shape — counting it as a defect would penalise honesty."""
    result = _gate(CAP).check(
        "The documents in scope do not address the arbitration seat anywhere."
    )
    assert result.uncited_claims == 0


# --------------------------------------------------- sentence segmentation


def test_a_clause_number_does_not_end_a_sentence() -> None:
    """Splitting inside `§ 9.2` hands the authority scanner fragments and
    invents a boundary mid-citation."""
    parts = split_sentences("The cap sits in § 9.2. Notice is sixty days.")
    assert len(parts) == 2
    assert "§ 9.2" in parts[0]


@pytest.mark.parametrize("abbrev", ["No.", "Art.", "e.g.", "Inc.", "v.", "U.S.C."])
def test_legal_abbreviations_do_not_end_a_sentence(abbrev: str) -> None:
    parts = split_sentences(f"See {abbrev} 4 for detail. Then it ends.")
    assert len(parts) == 2, parts


def test_ordinary_sentences_split() -> None:
    assert len(split_sentences("One thing. Two things. Three things.")) == 3


def test_empty_text_yields_nothing() -> None:
    assert split_sentences("") == []


# ------------------------------------- sentence-gated streaming (FR-10 + FR-22)


def _drain(gate: SentenceGate, tokens: list[str]) -> str:
    out = []
    for token in tokens:
        out.extend(gate.feed(token))
    out.extend(gate.flush())
    return "".join(out)


def test_clean_text_streams_through_unchanged() -> None:
    sg = SentenceGate(_gate(CAP))
    tokens = ["Liability is ", "capped at twelve ", "months of fees.[[1]] ", "That is all."]
    assert _drain(sg, tokens) == "".join(tokens)
    assert sg.blocked is False


def test_nothing_is_released_mid_sentence() -> None:
    """The whole point: a sentence is the smallest unit the authority scanner
    can judge, so nothing leaves before one completes."""
    sg = SentenceGate(_gate(CAP))
    assert list(sg.feed("Liability is capped")) == []
    assert list(sg.feed(" at twelve months")) == []
    assert sg.released_text == ""
    released = list(sg.feed(" of fees. "))
    assert released and "Liability" in released[0]


def test_a_fabricated_citation_is_never_emitted() -> None:
    """FR-22 enforced rather than merely detected. The bad sentence does not
    reach the attorney, and the stream stops."""
    sg = SentenceGate(_gate(CAP))
    emitted = _drain(
        sg,
        [
            "Liability is capped at twelve months of fees.[[1]] ",
            "See Roe v. Wade, 410 U.S. 113 (1973). ",
            "This never arrives.",
        ],
    )
    assert "Liability is capped" in emitted
    assert "Roe" not in emitted
    assert "410 U.S. 113" not in emitted
    assert "never arrives" not in emitted
    assert sg.blocked is True
    assert sg.blocked_on


def test_a_blocked_gate_stays_blocked() -> None:
    sg = SentenceGate(_gate(CAP))
    _drain(sg, ["See Roe v. Wade, 410 U.S. 113. "])
    assert sg.blocked is True
    assert list(sg.feed("More text. ")) == []
    assert list(sg.flush()) == []


def test_the_final_sentence_is_scanned_too() -> None:
    """An answer whose last sentence has no terminal punctuation must not slip
    past the scan on the flush path."""
    sg = SentenceGate(_gate(CAP))
    emitted = _drain(sg, ["The cap is twelve months.[[1]] See Roe v. Wade, 410 U.S. 113"])
    assert "Roe" not in emitted
    assert sg.blocked is True


def test_released_text_is_what_the_gate_then_checks() -> None:
    """`released_text` is the input to the final `check()`, so it must be
    exactly what the attorney saw — otherwise the persisted answer and the
    streamed one differ."""
    sg = SentenceGate(_gate(CAP))
    emitted = _drain(sg, ["A cap applies.[[1]] ", "Notice is sixty days.[[1]]"])
    assert sg.released_text == emitted
