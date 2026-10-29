"""The citation gate. FR-21, FR-22, and the reason NFR-15 can be enforced.

Three passes, in increasing severity.

1. **Resolution (FR-21).** Every `[[n]]` marker must name a source that was
   actually supplied. One that does not is stripped from the text, because a
   citation pointing nowhere is worse than no citation: it looks checkable.

2. **Authority (FR-22).** No case name, reporter citation, statute, rule or
   docket number may appear unless it appears verbatim in the supplied sources.
   This one **refuses the output**. A fabricated citation in a legal document is
   the single failure this product exists to prevent, and a plausible one is
   more dangerous than an obvious one.

3. **Groundedness.** Each cited sentence is checked for lexical overlap with the
   source it cites. This one *reports* rather than strips, by default: it is a
   proxy for entailment, and a proxy strict enough to catch real problems will
   also strip correct citations whose wording was paraphrased. The score lands
   on the trace, where it can be calibrated against a labelled set at M5.

**FR-10 versus FR-22.** Streaming token-by-token and never emitting a bad
citation are in direct tension: by the time a fabricated cite has been streamed,
it has been emitted. `SentenceGate` resolves it — tokens are buffered to
sentence boundaries, each completed sentence is scanned before release, and a
sentence that trips the authority check stops the stream instead of reaching the
attorney. The cost is about one sentence of latency, which is the right price.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from app.ai.orchestration.retrieval.ports import IndexedChunk

log = logging.getLogger("legallens.ai.citations")

#: `[[3]]`, the marker the ask prompt specifies.
CITE_MARKER = re.compile(r"\[\[(\d+)\]\]")

#: Legal authority patterns. Deliberately broad: a false positive costs one
#: check against the sources, while a false negative is a fabricated citation
#: reaching an attorney.
AUTHORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Smith v. Jones — two capitalised parties around "v."
    (
        "case name",
        re.compile(
            r"\b([A-Z][\w.'’-]+(?:\s+[A-Z][\w.'’-]+){0,3})"
            r"\s+v\.?\s+"
            r"([A-Z][\w.'’-]+(?:\s+[A-Z][\w.'’-]+){0,3})"
        ),
    ),
    # 410 U.S. 113 · 123 F.3d 456 · 55 Cal.App.4th 1
    (
        "reporter citation",
        re.compile(r"\b\d{1,4}\s+[A-Z][A-Za-z.]{1,12}(?:\.?\s?\d[a-z]{0,2})?\s+\d{1,5}\b"),
    ),
    # 28 U.S.C. § 1331 · 17 CFR 240
    (
        "statute citation",
        re.compile(r"\b\d{1,2}\s+(?:U\.?S\.?C\.?|C\.?F\.?R\.?)\s*§?\s*\d+[\w.()-]*"),
    ),
    # Fed. R. Civ. P. 12(b)(6)
    (
        "rule citation",
        re.compile(
            r"\b(?:Fed\.?\s*R\.?\s*(?:Civ|Crim|Evid|App)\.?\s*P\.?|FRCP)\s*\d+[\w.()-]*",
            re.I,
        ),
    ),
    # No. 21-cv-04567 · Case No. 2:19-cv-123
    (
        "docket number",
        re.compile(r"\b(?:No\.|Case\s+No\.?|Docket\s+No\.?)\s*[\d:]+[-–]\w{2,}[-–]?[\d]*", re.I),
    ),
)

#: Sentence boundary: terminal punctuation, then any closing quotes *and any
#: citation markers*, then whitespace.
#:
#: The `(?:\[\[\d+\]\])*` group is essential, not cosmetic. The ask prompt puts
#: the marker after the punctuation it supports — "...of fees.[[1]] " — which
#: leaves `[` sitting between the period and the space. Without this group no
#: boundary is found at all, sentences merge, and `SentenceGate` holds a clean
#: sentence hostage until a later one trips the authority scan.
_SENTENCE_END = re.compile(
    r"(?<=[.!?])"  # terminal punctuation
    r"[\"'’”\)\]]*"  # closing quotes or brackets
    r"(?:\[\[\d+\]\])*"  # ...and any citation markers sitting after it
    r"\s+"
)

#: Abbreviations whose trailing period is not a sentence end. Without these,
#: "§ 9.2 of the Agreement, see No. 4" splits mid-clause and the gate scans
#: fragments.
_PROTECTED = (
    "no.",
    "nos.",
    "art.",
    "arts.",
    "sec.",
    "secs.",
    "para.",
    "paras.",
    "cl.",
    "v.",
    "vs.",
    "inc.",
    "llc.",
    "ltd.",
    "co.",
    "corp.",
    "ch.",
    "pp.",
    "p.",
    "e.g.",
    "i.e.",
    "cf.",
    "id.",
    "ibid.",
    "al.",
    "jr.",
    "sr.",
    "u.s.",
    "u.s.c.",
    "f.3d",
    "f.2d",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "ex.",
    "fig.",
)


#: Words that introduce a citation but are not part of the party name. The
#: case-name pattern allows up to three capitalised words before "v.", so
#: "See Smith v. Jones" captures "See" as a party — and then fails to match a
#: source that says "per Smith v. Jones". Trimmed before comparison.
_SIGNAL_WORDS = frozenset(
    [
        "see",
        "also",
        "cf",
        "accord",
        "but",
        "compare",
        "contra",
        "citing",
        "quoting",
        "in",
        "under",
        "per",
        "following",
        "e.g",
        "eg",
        "i.e",
        "ie",
        "id",
        "ibid",
        "and",
        "the",
        "of",
        "from",
        "held",
        "holds",
        "decision",
        "ruling",
        "case",
    ]
)


def _trim_signal_words(text: str) -> str:
    words = text.split()
    while words and words[0].strip(".,;:").casefold() in _SIGNAL_WORDS:
        words.pop(0)
    return " ".join(words)


def _normalise(text: str) -> str:
    """Collapse whitespace and casefold, for substring comparison against
    source text that may have been re-wrapped by extraction."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(frozen=True)
class AuthorityFinding:
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.text!r}"


@dataclass
class GateResult:
    """What the gate did, in enough detail for a trace and a refusal."""

    text: str
    #: Source numbers that survived, in first-appearance order.
    cited: list[int] = field(default_factory=list)
    #: Markers removed because they named no supplied source (FR-21).
    stripped: list[int] = field(default_factory=list)
    #: Authority absent from the sources (FR-22). Non-empty means refuse.
    fabricated: list[AuthorityFinding] = field(default_factory=list)
    #: Sentences carrying a claim but no citation.
    uncited_claims: int = 0
    #: Per-citation lexical overlap with the source it cites, 0-1.
    groundedness: dict[int, float] = field(default_factory=dict)

    @property
    def must_refuse(self) -> bool:
        return bool(self.fabricated)

    @property
    def weakest_groundedness(self) -> float | None:
        return min(self.groundedness.values()) if self.groundedness else None


class CitationGate:
    """Verifies generated text against the sources it was given."""

    def __init__(
        self,
        sources: list[IndexedChunk],
        *,
        groundedness_floor: float = 0.0,
        strip_ungrounded: bool = False,
    ) -> None:
        """`sources` is 1-indexed by position — source `n` is `sources[n-1]`,
        matching the `n` attribute the prompt renders."""
        self.sources = sources
        self.groundedness_floor = groundedness_floor
        self.strip_ungrounded = strip_ungrounded
        self._haystack = _normalise(" \n ".join(c.text for c in sources))

    # ------------------------------------------------------- authority (FR-22)

    def unsupported_authority(self, text: str) -> list[AuthorityFinding]:
        """Authority in `text` that appears in no supplied source.

        Matched against the normalised concatenation of every source, so a
        citation the documents genuinely contain passes however it is spaced.
        """
        findings: list[AuthorityFinding] = []
        seen: set[str] = set()
        for kind, pattern in AUTHORITY_PATTERNS:
            for match in pattern.finditer(text):
                raw = _trim_signal_words(match.group(0).strip()).strip(" ,;:")
                key = _normalise(raw)
                # A trimmed-to-nothing match was all signal words, not a cite.
                if not key or key in seen or len(key) < 4:
                    continue
                seen.add(key)
                # The statute and docket patterns end in a greedy character
                # class, so they swallow the sentence's full stop: the match is
                # "28 U.S.C. § 1331." while the source says "...§ 1331 per".
                # Both forms are checked rather than stripping periods outright,
                # since "U.S.C." needs its own.
                if not any(
                    candidate in self._haystack
                    for candidate in (key, key.rstrip(". "))
                    if candidate
                ):
                    findings.append(AuthorityFinding(kind=kind, text=raw.rstrip(". ")))
        return findings

    # ------------------------------------------------------------- full gate

    def check(self, text: str) -> GateResult:
        valid = set(range(1, len(self.sources) + 1))
        result = GateResult(text=text)

        # Pass 1 — resolution (FR-21).
        for match in CITE_MARKER.finditer(text):
            n = int(match.group(1))
            if n in valid:
                if n not in result.cited:
                    result.cited.append(n)
            elif n not in result.stripped:
                result.stripped.append(n)

        if result.stripped:
            log.warning("stripping unresolvable citations: %s", result.stripped)
            text = CITE_MARKER.sub(
                lambda m: "" if int(m.group(1)) not in valid else m.group(0), text
            )
            # Removing a marker leaves a space before the punctuation it sat in
            # front of.
            text = re.sub(r"\s+([.,;:)])", r"\1", text)
            result.text = text

        # Pass 2 — authority (FR-22). Hard stop.
        result.fabricated = self.unsupported_authority(text)
        if result.fabricated:
            log.error(
                "FR-22: output cites authority absent from sources: %s",
                "; ".join(str(f) for f in result.fabricated),
            )

        # Pass 3 — groundedness, reported.
        result.groundedness = self._groundedness(text)
        result.uncited_claims = self._uncited_claims(text)

        if self.strip_ungrounded:
            weak = [
                n for n, score in result.groundedness.items() if score < self.groundedness_floor
            ]
            if weak:
                log.warning("stripping ungrounded citations: %s", weak)
                result.text = CITE_MARKER.sub(
                    lambda m: "" if int(m.group(1)) in weak else m.group(0), result.text
                )
                result.cited = [n for n in result.cited if n not in weak]
                result.stripped.extend(weak)

        return result

    # ------------------------------------------------------------- internals

    def _groundedness(self, text: str) -> dict[int, float]:
        """Lexical overlap between each cited sentence and its source.

        A proxy for entailment, not entailment. Content words only — stopword
        overlap would score every sentence highly and measure nothing.
        """
        scores: dict[int, float] = {}
        for sentence in split_sentences(text):
            numbers = [int(m.group(1)) for m in CITE_MARKER.finditer(sentence)]
            if not numbers:
                continue
            claim = CITE_MARKER.sub("", sentence)
            words = _content_words(claim)
            if not words:
                continue
            for n in numbers:
                if not 1 <= n <= len(self.sources):
                    continue
                source_words = _content_words(self.sources[n - 1].text)
                overlap = len(words & source_words) / len(words)
                # Keep the best evidence for a source cited more than once.
                scores[n] = max(scores.get(n, 0.0), round(overlap, 3))
        return scores

    @staticmethod
    def _uncited_claims(text: str) -> int:
        """Sentences that assert something about the documents and cite nothing.

        Counted, not corrected — the prompt asks for a citation on every
        assertion, and this is how we find out whether it complied.
        """
        count = 0
        for sentence in split_sentences(text):
            if CITE_MARKER.search(sentence):
                continue
            stripped = sentence.strip()
            if len(stripped.split()) < 6:
                continue
            # A sentence saying the documents are silent is a legitimate
            # uncited claim — there is nothing to cite.
            if re.search(
                r"\b(do(?:es)? not address|not addressed|silent|no provision|"
                r"nothing in|would require|do(?:es)? not contain|not present)\b",
                stripped,
                re.I,
            ):
                continue
            count += 1
        return count


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "shall",
        "may",
        "must",
        "not",
        "no",
        "any",
        "such",
        "which",
        "who",
        "whom",
        "their",
        "there",
        "these",
        "those",
        "then",
        "than",
        "if",
        "when",
        "where",
        "while",
        "under",
        "upon",
        "per",
    ]
)


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9§.\-]{2,}", text.casefold()) if w not in _STOPWORDS}


def split_sentences(text: str) -> list[str]:
    """Sentence split that survives legal abbreviations.

    Slices on match positions rather than using `re.split`, because the
    boundary pattern *consumes* trailing citation markers — `re.split` would
    drop every `[[n]]` that sits at a sentence end, which is where the prompt
    puts them. Groundedness scoring then saw no citations at all.

    Abbreviations are handled by skipping boundaries that fall after one, so
    "No. 4" and "e.g." do not end a sentence.
    """
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if _ends_mid_abbreviation(text[start : match.start() + 1]):
            continue
        pieces.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        pieces.append(text[start:])
    return [p for p in pieces if p.strip()]


def _ends_mid_abbreviation(text: str) -> bool:
    """Whether a period here is part of an abbreviation rather than a full stop.

    A clause number needs no rule of its own. `_SENTENCE_END` requires
    whitespace after the period, so "9.2.1" and "§ 12.3(a)" can never produce a
    boundary — while "the cap sits in § 9.2. Notice is..." genuinely is two
    sentences. An earlier version also protected any digit-period-digit tail,
    which suppressed that legitimate boundary and merged the two.
    """
    tail = text.rstrip().casefold()
    return any(tail.endswith(a) for a in _PROTECTED)


class SentenceGate:
    """Buffers a token stream to sentence boundaries, scanning before release.

    This is what lets FR-10 and FR-22 both hold. Tokens accumulate; when a
    sentence completes it is scanned for unsupported authority; clean sentences
    are released and a dirty one halts the stream, so a fabricated citation is
    never emitted rather than merely detected afterwards.
    """

    def __init__(self, gate: CitationGate) -> None:
        self.gate = gate
        self._buffer = ""
        self._released = ""
        self.blocked_on: list[AuthorityFinding] = []

    @property
    def released_text(self) -> str:
        return self._released

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_on)

    def feed(self, token: str) -> Iterator[str]:
        """Yields whatever is now safe to emit. Yields nothing mid-sentence."""
        if self.blocked:
            return
        self._buffer += token
        while True:
            boundary = self._next_boundary()
            if boundary is None:
                return
            sentence, self._buffer = self._buffer[:boundary], self._buffer[boundary:]
            findings = self.gate.unsupported_authority(sentence)
            if findings:
                self.blocked_on = findings
                log.error(
                    "FR-22: halting stream, sentence cites unsupported authority: %s",
                    "; ".join(str(f) for f in findings),
                )
                return
            self._released += sentence
            yield sentence

    def flush(self) -> Iterator[str]:
        """Release the final, unterminated sentence — scanned like any other."""
        if self.blocked or not self._buffer:
            return
        tail, self._buffer = self._buffer, ""
        findings = self.gate.unsupported_authority(tail)
        if findings:
            self.blocked_on = findings
            return
        self._released += tail
        yield tail

    def _next_boundary(self) -> int | None:
        for match in _SENTENCE_END.finditer(self._buffer):
            end = match.end()
            if _ends_mid_abbreviation(self._buffer[: match.start() + 1]):
                continue
            return end
        return None
