"""The intent router: one LangChain call to Claude Haiku 4.5 per routed turn.

Shape of the thing
------------------
    RouterInput
      -> explicit mode?        -> done, no tokens spent
      -> empty text?           -> done, no tokens spent
      -> ChatPromptTemplate
      -> ChatAnthropic.with_structured_output(..., method="json_schema")
      -> IntentDecision

Three decisions worth knowing before editing this file.

**`method="json_schema"`, not tool calling.** LangChain's default for
`with_structured_output` is forced tool use. Claude's native structured outputs
constrain decoding against the schema instead, so a malformed label is not
something that has to be handled — it cannot be generated. The tool-calling path
also costs a tool definition in every request and fails differently on models
that reject forced tool use.

**No thinking, no `effort`.** Haiku 4.5 predates adaptive thinking and takes
`budget_tokens`; `effort` errors on it outright. Neither belongs on a classifier
anyway — the rationale field is the only reasoning this task needs, and it is
generated before the label precisely so it functions as reasoning.

**The router never fails.** It returns an `IntentDecision` in every path, with
`source` saying how it got there and `error` set when the model was not reached.
NFR-6 requires the turn to survive a provider outage, and routing is the first
thing in the turn. `on_failure="raise"` opts out, for eval harnesses that must
not score a fallback as a prediction.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from app.ai.orchestration.heuristics import classify_by_keyword_or_default
from app.ai.orchestration.intent import (
    SAFE_DEFAULT,
    IntentClassification,
    IntentDecision,
    RouterInput,
    Usage,
)
from app.ai.orchestration.prompt import IntentPrompt, load_prompt
from app.ai.orchestration.settings import RouterSettings, get_router_settings

log = logging.getLogger("legallens.ai.router")

#: Claude Haiku 4.5 declines to cache a prefix shorter than this and says
#: nothing about it — `cache_creation_input_tokens` is simply 0. Checked so a
#: marker that does nothing is logged once rather than mistaken for a working
#: optimisation. The floor is not monotonic across models: it is 512 on Opus 5
#: and 4096 here.
_HAIKU_CACHE_FLOOR_TOKENS = 4096

_HUMAN_TEMPLATE = """\
{history_block}Documents in scope: {scope_line}

The attorney's request:
<request>
{text}
</request>

Classify it."""


class RouterUnavailable(RuntimeError):
    """Raised only when `on_failure="raise"`.

    Separate from `app.ai.ports.AiUnavailable` so this module stays importable
    without the port, and so an eval harness can catch routing failures without
    catching generation failures.
    """


class IntentRouter:
    """Classifies one request into `ask | summary | draft | out_of_scope`.

    Stateless after construction, and the LangChain runnable is built once:
    rebuilding it per turn would re-read the prompt file and re-create an HTTP
    client on the NFR-1 critical path.
    """

    def __init__(
        self,
        settings: RouterSettings | None = None,
        *,
        chat_model: Any | None = None,
        prompt: IntentPrompt | None = None,
    ) -> None:
        """`chat_model` injects a double. Tests pass a fake runnable and never
        touch the network; `scripts/try_intent_router.py` passes nothing."""
        self.settings = settings or get_router_settings()
        self.prompt = prompt or load_prompt()
        self.model_name = self.settings.model
        self.prompt_version = self.prompt.version
        self._cache_system = self._should_cache()
        self._chain = self._build_chain(chat_model)

    # ------------------------------------------------------------- building

    def _should_cache(self) -> bool:
        if not self.settings.cache_system_prompt:
            return False
        if self.prompt.approx_tokens < _HAIKU_CACHE_FLOOR_TOKENS:
            log.info(
                "not requesting a cache breakpoint: the system prompt is ~%d tokens and "
                "%s will not cache a prefix below %d",
                self.prompt.approx_tokens,
                self.settings.model,
                _HAIKU_CACHE_FLOOR_TOKENS,
            )
            return False
        return True

    def _system_content(self) -> str | list[dict[str, Any]]:
        if not self._cache_system:
            return self.prompt.system
        return [
            {
                "type": "text",
                "text": self.prompt.system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic

        if not self.settings.has_credentials:
            raise RouterUnavailable(
                "No Anthropic credentials. Set ANTHROPIC_API_KEY (or LL_AI_API_KEY)."
            )
        return ChatAnthropic(
            model=self.settings.model,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            timeout=self.settings.timeout_s,
            max_retries=self.settings.max_retries,
            api_key=self.settings.api_key,
        )

    def _build_chain(self, chat_model: Any | None) -> Runnable[dict[str, Any], Any]:
        model = chat_model if chat_model is not None else self._build_model()
        structured = model.with_structured_output(
            IntentClassification,
            method="json_schema",
            # The raw message carries token usage, which NFR-8 and NFR-11 both
            # want, and it turns a parse failure into a returned error rather
            # than an exception mid-chain.
            include_raw=True,
        )
        template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", _HUMAN_TEMPLATE)]
        )
        return template | structured

    # -------------------------------------------------------------- prompt

    def _history_block(self, history: tuple[tuple[str, str], ...]) -> str:
        if not history:
            return ""
        recent = history[-self.settings.history_turns :]
        lines: list[str] = []
        for role, text in recent:
            clipped = text.strip().replace("\n", " ")
            if len(clipped) > self.settings.history_chars:
                clipped = clipped[: self.settings.history_chars].rstrip() + "…"
            lines.append(f"{role}: {clipped}")
        return "Recent conversation (oldest first):\n" + "\n".join(lines) + "\n\n"

    def _variables(self, request: RouterInput) -> dict[str, Any]:
        return {
            "system": self._system_content(),
            "history_block": self._history_block(request.history),
            "scope_line": request.scope.as_prompt_line(),
            "text": request.text.strip(),
        }

    # ------------------------------------------------------------ routing

    async def aclassify(self, request: RouterInput) -> IntentDecision:
        """The entry point the orchestration workflow will call.

        Async because every caller is: a blocking HTTP call inside the event
        loop would stall every other in-flight turn, and the port's current
        synchronous `route_intent` has to become `async` at integration. A sync
        `classify` exists for scripts and tests, not for the request path.
        """
        shortcut = self._shortcut(request)
        if shortcut is not None:
            return shortcut

        started = time.perf_counter()
        try:
            raw = await self._chain.ainvoke(self._variables(request))
        except Exception as exc:  # noqa: BLE001 — every provider failure degrades alike
            return self._on_failure(request, f"{type(exc).__name__}: {exc}")
        return self._decide(request, raw, started)

    def classify(self, request: RouterInput) -> IntentDecision:
        """Synchronous twin of `aclassify`, for the CLI harness and tests."""
        shortcut = self._shortcut(request)
        if shortcut is not None:
            return shortcut

        started = time.perf_counter()
        try:
            raw = self._chain.invoke(self._variables(request))
        except Exception as exc:  # noqa: BLE001
            return self._on_failure(request, f"{type(exc).__name__}: {exc}")
        return self._decide(request, raw, started)

    # ----------------------------------------------------------- internals

    def _shortcut(self, request: RouterInput) -> IntentDecision | None:
        """The two cases that must not reach the model.

        The mode chip is the important one, and it is a product decision before
        it is a cost decision: an attorney who pressed "Summary" should not have
        a classifier second-guess them (BRD §7, frontend nuance #4). It is also
        the single largest saving here, since every explicit-mode turn routes
        for free.
        """
        if request.mode != "auto":
            return IntentDecision(
                intent=request.mode,
                confidence=1.0,
                rationale=f"The attorney selected {request.mode!r}; the router is not consulted.",
                source="explicit_mode",
                model=self.model_name,
                prompt_version=self.prompt_version,
                trace_id=request.trace_id,
            )
        if not request.text.strip():
            return IntentDecision(
                intent=SAFE_DEFAULT,
                confidence=0.0,
                rationale="Empty request; nothing to classify.",
                source="default_fallback",
                model=self.model_name,
                prompt_version=self.prompt_version,
                trace_id=request.trace_id,
            )
        return None

    def _decide(self, request: RouterInput, raw: Any, started: float) -> IntentDecision:
        """Unpack the `include_raw=True` envelope: `{raw, parsed, parsing_error}`."""
        latency_ms = (time.perf_counter() - started) * 1000
        parsed = raw.get("parsed") if isinstance(raw, dict) else None
        message = raw.get("raw") if isinstance(raw, dict) else None
        parsing_error = raw.get("parsing_error") if isinstance(raw, dict) else None
        usage = _usage_from(message, latency_ms)

        if parsed is None or not isinstance(parsed, IntentClassification):
            # Structured outputs make this close to unreachable; it is handled
            # because "close to" is not "never", and a refusal or a max_tokens
            # cut-off both land here.
            detail = f"{parsing_error}" if parsing_error else "the model returned no parse"
            return self._on_failure(request, f"unparseable classification: {detail}", usage)

        return IntentDecision(
            intent=parsed.intent,
            confidence=parsed.confidence,
            rationale=parsed.rationale.strip(),
            source="model",
            usage=usage,
            model=self.model_name,
            prompt_version=self.prompt_version,
            trace_id=request.trace_id,
        )

    def _on_failure(
        self, request: RouterInput, error: str, usage: Usage | None = None
    ) -> IntentDecision:
        log.warning("intent routing failed (%s): %s", self.settings.on_failure, error)
        if self.settings.on_failure == "raise":
            raise RouterUnavailable(error)

        if self.settings.on_failure == "heuristic":
            intent, why = classify_by_keyword_or_default(request.text)
            source = "heuristic_fallback" if intent != SAFE_DEFAULT else "default_fallback"
            rationale = f"Router unavailable; fell back to keyword rules ({why})."
        else:
            intent, source = SAFE_DEFAULT, "default_fallback"
            rationale = "Router unavailable; fell back to the safe default."

        return IntentDecision(
            intent=intent,
            confidence=0.0,
            rationale=rationale,
            source=source,  # type: ignore[arg-type]  # narrowed by construction above
            usage=usage or Usage(),
            error=error,
            model=self.model_name,
            prompt_version=self.prompt_version,
            trace_id=request.trace_id,
        )


def _usage_from(message: BaseMessage | None, latency_ms: float) -> Usage:
    """Token counts out of LangChain's `usage_metadata`.

    Read defensively: a fake model in a test has no usage, and the cache
    counters live in a nested `input_token_details` that is absent unless a
    breakpoint was actually honoured.
    """
    meta: dict[str, Any] = getattr(message, "usage_metadata", None) or {}
    details: dict[str, Any] = meta.get("input_token_details") or {}
    return Usage(
        input_tokens=int(meta.get("input_tokens") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
        cache_read_input_tokens=int(details.get("cache_read") or 0),
        cache_creation_input_tokens=int(details.get("cache_creation") or 0),
        latency_ms=latency_ms,
    )


_router: IntentRouter | None = None


def get_router() -> IntentRouter:
    """Process-wide router.

    Not `lru_cache`, because construction opens an HTTP client and a cached
    exception from a missing API key would be sticky for the life of the
    process — `reset_router()` plus an explicit global makes the retry obvious.
    """
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router


def reset_router() -> None:
    global _router
    _router = None
