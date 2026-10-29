"""STUB. A real document-type classifier is M4.

Heuristics over the filename and the first pages' text, deliberately mirroring
the rules `frontend/src/api/client.ts` used, so a document classified in the
mock is classified the same way now.

FR-5 only requires that a type is detected, shown, and overridable — and the
override is what makes a wrong guess cheap. Low confidence is reported rather
than hidden, which is what drives the "review the type" hint in the UI.
"""

from __future__ import annotations

import re

from app.ingestion.ports import Classification, ParsedPage

NAME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("contract", re.compile(r"agreement|contract|nda|amendment|lease|msa|sow|addendum", re.I)),
    ("opinion", re.compile(r"\bv\.\s|\bv\b\s|opinion|f\.\d|f\.\dth|wl \d|s\.ct\.|cir\.", re.I)),
    ("pleading", re.compile(r"complaint|motion|petition|answer|brief|pleading|summons", re.I)),
)

TEXT_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "contract",
        (
            "this agreement",
            "entered into as of",
            "the parties agree",
            "in witness whereof",
            "governing law",
            "effective date",
        ),
    ),
    (
        "opinion",
        (
            "we affirm",
            "we reverse",
            "we hold",
            "appellant",
            "the district court",
            "jurisdiction under 28 u.s.c.",
            "dissenting",
        ),
    ),
    (
        "pleading",
        (
            "wherefore",
            "plaintiff",
            "defendant",
            "jury trial demanded",
            "cause of action",
            "re-alleges and incorporates",
            "in the united states district court",
        ),
    ),
)


class StubClassifier:
    name = "classifier/heuristic-m3"

    def classify(self, filename: str, pages: list[ParsedPage]) -> Classification:
        sample = "\n".join(p.text for p in pages[:6]).lower()[:20_000]

        scores: dict[str, float] = {"contract": 0.0, "opinion": 0.0, "pleading": 0.0}

        for doc_type, pattern in NAME_PATTERNS:
            if pattern.search(filename):
                # The filename is strong evidence — a legal filename is written
                # by a person who knows what the document is.
                scores[doc_type] += 2.0

        for doc_type, signals in TEXT_SIGNALS:
            hits = sum(1 for s in signals if s in sample)
            scores[doc_type] += hits * 0.9

        best_type = max(scores, key=lambda k: scores[k])
        best = scores[best_type]
        if best < 1.5:
            # Below this, saying "other" with a low confidence is more useful
            # than a confident guess the attorney has to notice and undo.
            return Classification("other", round(min(0.62, 0.35 + best * 0.1), 2))

        runner_up = max((v for k, v in scores.items() if k != best_type), default=0.0)
        margin = (best - runner_up) / best
        confidence = min(0.97, 0.62 + margin * 0.3 + min(best, 6.0) * 0.03)
        return Classification(best_type, round(confidence, 2))
