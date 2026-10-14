"""The three generation workflows. All three are implemented."""

from __future__ import annotations

from app.ai.orchestration.workflows.ask import AskOutcome, AskWorkflow
from app.ai.orchestration.workflows.draft import DraftOutcome, DraftWorkflow
from app.ai.orchestration.workflows.stages import STAGE_LABEL, StageClock
from app.ai.orchestration.workflows.summary import (
    FieldExtraction,
    SummaryOutcome,
    SummaryWorkflow,
)

__all__ = [
    "STAGE_LABEL",
    "AskOutcome",
    "AskWorkflow",
    "DraftOutcome",
    "DraftWorkflow",
    "FieldExtraction",
    "StageClock",
    "SummaryOutcome",
    "SummaryWorkflow",
]
