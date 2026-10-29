"""Loads `intent_classification.prompt.md`.

The prompt is a file, not a string literal, for three reasons. It is subject
matter expertise and should be editable by someone who does not read Python. It
is the thing an eval hill-climbs, so it changes far more often than the code
around it. And NFR-12 requires the prompt version to be recorded on every
generation, which is only honest if the recorded version is derived from the
bytes actually sent.

Hence `prompt_version` is `<declared version>+<8-char content hash>`: editing
the prompt without bumping the front-matter version still produces a different
stamp, so two answers that disagree can be told apart in the audit log.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Every `*.prompt.md` lives here, so adding a workflow adds a file rather than
#: a path convention.
PROMPTS_DIR = Path(__file__).with_name("prompts")

PROMPT_PATH = PROMPTS_DIR / "intent_classification.prompt.md"

_SYSTEM_BEGIN = "<!-- SYSTEM PROMPT BEGIN -->"
_SYSTEM_END = "<!-- SYSTEM PROMPT END -->"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_EXAMPLE_ROW = re.compile(r"^\|(?!\s*-)(.+)\|\s*$", re.M)

#: First-column names that mark a table *header* rather than a case.
#:
#: One set rather than a literal per call site: the gold table's first column is
#: named for what the prompt classifies, so it is `question` for the router,
#: `field` for summary extraction and `section` for draft generation. A header
#: not listed here is silently parsed as a labelled example, which shows up as a
#: phantom case with `expected="expect"` — a test asserting on the gold set then
#: fails for a reason that has nothing to do with the gold set.
_HEADER_CELLS = frozenset({"question", "request", "field", "section", "input"})


class PromptFileError(RuntimeError):
    """The prompt file is missing or malformed.

    Raised at load rather than tolerated: a router running on a silently
    truncated prompt would classify badly and look healthy.
    """


@dataclass(frozen=True)
class Example:
    """One row of the labelled-examples table.

    `taught` matters for reading an eval honestly. The prompt teaches with
    illustrative tables of its own, and some gold cases are deliberately the
    same ones — they check that the prompt's own rules are followed. A case
    that appears verbatim in the prompt is not evidence of generalisation
    though, so the two groups are scored separately rather than averaged into
    one misleading number.
    """

    question: str
    mode: str
    expected: str
    note: str = ""
    #: This exact question appears in an illustrative table inside the prompt.
    taught: bool = False


@dataclass(frozen=True)
class IntentPrompt:
    #: Exactly the text sent as the system prompt.
    system: str
    #: `version` from the front matter.
    declared_version: str
    #: `<declared_version>+<hash of system>`. What NFR-12 records.
    version: str
    #: The gold set. Not sent to the model — see the prompt file.
    examples: tuple[Example, ...]

    @property
    def taught_examples(self) -> tuple[Example, ...]:
        """Cases the prompt states outright. A regression here means the model
        is not following an explicit instruction."""
        return tuple(e for e in self.examples if e.taught)

    @property
    def held_out_examples(self) -> tuple[Example, ...]:
        """Cases the prompt does not state. The only group that measures
        generalisation."""
        return tuple(e for e in self.examples if not e.taught)

    @property
    def approx_tokens(self) -> int:
        """Rough count for the prompt-caching floor check in `router.py`.

        ~3.7 characters per token is a reasonable English prose estimate. Only
        used to decide whether to bother asking for a cache breakpoint, never
        for billing — `Usage` carries the real numbers from the response.
        """
        return len(self.system) // 4


def _front_matter(raw: str, filename: str = "") -> dict[str, str]:
    name = filename or PROMPT_PATH.name
    match = _FRONT_MATTER.search(raw)
    if match is None:
        raise PromptFileError(f"{name} has no YAML front matter")
    pairs: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            pairs[key.strip()] = value.strip()
    return pairs


def _system_prompt(raw: str, filename: str = "") -> str:
    name = filename or PROMPT_PATH.name
    start = raw.find(_SYSTEM_BEGIN)
    end = raw.find(_SYSTEM_END)
    if start < 0 or end < 0:
        raise PromptFileError(
            f"{name} must delimit the system prompt with {_SYSTEM_BEGIN} and {_SYSTEM_END}"
        )
    if end < start:
        raise PromptFileError(f"{name}: END marker precedes BEGIN marker")
    body = raw[start + len(_SYSTEM_BEGIN) : end].strip()
    if not body:
        raise PromptFileError(f"{name}: the system prompt is empty")
    return body


def _table_cells(block: str) -> set[str]:
    """First cell of every markdown table row in `block`, normalised.

    Used to decide `Example.taught` by exact cell match. A substring test would
    be useless here: "hi" and "indemnification" occur in ordinary prose
    throughout the prompt.
    """
    cells: set[str] = set()
    for row in _EXAMPLE_ROW.finditer(block):
        first = row.group(1).split("|")[0].strip().strip('"').rstrip(".?!").casefold()
        if first and first not in _HEADER_CELLS:
            cells.add(first)
    return cells


def _examples(raw: str) -> tuple[Example, ...]:
    """Parses the gold-set table that follows the system prompt.

    Rows *before* the END marker are the prompt's own illustrative tables and
    are not cases; they are read only to mark which gold cases the prompt
    already states.
    """
    end = raw.find(_SYSTEM_END)
    taught = _table_cells(raw[:end]) if end >= 0 else set()
    tail = raw[end + len(_SYSTEM_END) :] if end >= 0 else raw

    found: list[Example] = []
    for row in _EXAMPLE_ROW.finditer(tail):
        cells = [c.strip() for c in row.group(1).split("|")]
        if len(cells) < 3 or cells[0].casefold() in _HEADER_CELLS:
            continue
        question = cells[0]
        found.append(
            Example(
                question=question,
                mode=cells[1],
                expected=cells[2],
                note=cells[3] if len(cells) > 3 else "",
                taught=question.strip('"').rstrip(".?!").casefold() in taught,
            )
        )
    return tuple(found)


@lru_cache(maxsize=1)
def load_prompt(path: Path | None = None) -> IntentPrompt:
    """The intent router's prompt. Kept as its own function because the router
    was built against it and its 62 tests name it.

    Tests that edit the file call `load_prompt.cache_clear()`.
    """
    return _parse(path or PROMPT_PATH)


@lru_cache(maxsize=16)
def load_named_prompt(name: str) -> IntentPrompt:
    """Any prompt in `prompts/`, by file name or bare stem.

    `load_named_prompt("query_rewrite")` and
    `load_named_prompt("query_rewrite.prompt.md")` are the same file. Cached per
    name, because a prompt is immutable configuration and re-reading it per turn
    would put file I/O on the latency path.
    """
    filename = name if name.endswith(".prompt.md") else f"{name}.prompt.md"
    return _parse(PROMPTS_DIR / filename)


def available_prompts() -> tuple[str, ...]:
    """Stems of every prompt on disk. Used by a test that asserts each one
    parses, so a malformed prompt fails the suite rather than a request."""
    return tuple(sorted(p.name.removesuffix(".prompt.md") for p in PROMPTS_DIR.glob("*.prompt.md")))


def _parse(target: Path) -> IntentPrompt:
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptFileError(f"cannot read {target}: {exc}") from exc

    system = _system_prompt(raw, target.name)
    declared = _front_matter(raw, target.name).get("version", "unversioned")
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()[:8]
    return IntentPrompt(
        system=system,
        declared_version=declared,
        version=f"{declared}+{digest}",
        examples=_examples(raw),
    )
