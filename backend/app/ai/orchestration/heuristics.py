"""The keyword router, kept only as a fallback for when the model is unreachable.

These are the rules `FixtureEngine.route_intent` has shipped with since M3, and
they are already covered by the contract tests. That is the entire argument for
reusing them here: when the provider is down, NFR-6 says the turn still has to
work, and a tested heuristic is a better degraded mode than a blind `ask` for
every request — "draft me a memo" routed to `ask` during an outage is a visibly
wrong answer, where the heuristic gets it right without a network call.

They are *not* a pre-filter. Running these before the model would make the
model's judgement unreachable for exactly the inputs it was added to handle.
"""

from __future__ import annotations

import re

from app.ai.orchestration.intent import SAFE_DEFAULT, Intent

# `summar` is a prefix, not a word: a trailing \b never matches "summarise".
#
# One deliberate divergence from `FixtureEngine`: the negative lookahead on
# `brief`. The fixture engine's draft rule matches a bare `brief` and is tested
# first, so "brief me on this contract" routes to `draft` there — it starts
# writing a document when the attorney asked to be told what is in one. `brief`
# is a noun in "case brief" and a verb in "brief me", and only the second is a
# summary; the lookahead lets the summary rule see it. The prompt calls out the
# same ambiguity, so the model and the fallback now agree.
#
# `FixtureEngine` is left alone on purpose: it is pinned to the frontend mock by
# the M3 contract tests, and changing its routing is an M4 integration concern,
# not a fallback concern.
_DRAFT_RE = re.compile(r"\b(draft|memo|memorandum|brief(?!\s+me\b)|write me|prepare a)\w*", re.I)
_SUMMARY_RE = re.compile(
    r"\b(summar\w*|overview|what(?:'s| is) in|walk me through|key terms|brief me)", re.I
)
_OUT_OF_SCOPE_RE = re.compile(r"\b(weather|recipe|stock price|who won|translate)\w*", re.I)


def classify_by_keyword(text: str) -> tuple[Intent, str] | None:
    """`(intent, which rule fired)`, or None when no rule matches.

    None rather than `ask` so the caller can tell "the heuristic recognised
    this" from "the heuristic had nothing to say and the default applied" — two
    different things in a trace.

    Order matters and matches the fixture engine: `draft` is tested first, so
    "draft a memo summarising the lease" routes to `draft`, which is precedence
    rule 1 in the prompt.
    """
    if _DRAFT_RE.search(text):
        return "draft", "draft keyword"
    if _SUMMARY_RE.search(text):
        return "summary", "summary keyword"
    if _OUT_OF_SCOPE_RE.search(text):
        return "out_of_scope", "out-of-scope keyword"
    return None


def classify_by_keyword_or_default(text: str) -> tuple[Intent, str]:
    match = classify_by_keyword(text)
    if match is not None:
        return match
    return SAFE_DEFAULT, "no keyword matched"
