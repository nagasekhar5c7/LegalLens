"""NFR-14, enforced rather than asserted.

"The AI layer is reachable only via `answer()`, `summarize()`, `draft()`; the
backend holds no retrieval or prompt logic."

That is a statement about imports, so it is checkable by reading them. Without
this test, NFR-14 degrades the first time someone reaches into the fixtures from
a route to fix something quickly — and M4 then has to untangle it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

#: Layers that must not know how answers are produced.
BACKEND_PACKAGES = ("api", "services", "middleware", "storage", "ingestion")

#: The AI layer's internals. `app.ai.ports`, `app.ai.disclaimers` and
#: `app.ai.scenarios` are the public surface and are deliberately excluded.
AI_INTERNALS = ("app.ai.fixtures", "app.ai.fixture_engine")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _runtime_imports(path: Path) -> set[str]:
    """Imports that actually execute, excluding `if TYPE_CHECKING:` blocks.

    The distinction is load-bearing rather than pedantic. A module may name a
    type it never imports at runtime, and that is how
    `orchestration/intent.py` annotates `RouterInput.from_ai_request(request:
    AiRequest)` without taking a dependency on the port — the annotation is a
    string under `from __future__ import annotations`, and the import is erased.
    A rule that could not tell the two apart would forbid the type hint along
    with the dependency.
    """
    tree = ast.parse(path.read_text(), filename=str(path))

    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for inner in node.body:
                for descendant in ast.walk(inner):
                    guarded.add(id(descendant))

    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _modules_in(package: str) -> list[Path]:
    return sorted((APP / package).rglob("*.py"))


@pytest.mark.parametrize("package", BACKEND_PACKAGES)
def test_backend_layers_do_not_import_ai_internals(package: str) -> None:
    offenders: list[str] = []
    for path in _modules_in(package):
        for imported in _imports(path):
            if any(imported.startswith(internal) for internal in AI_INTERNALS):
                offenders.append(f"{path.relative_to(APP.parent)} imports {imported}")

    assert not offenders, (
        "NFR-14: the backend must reach the AI layer only through its ports.\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_routes_reach_the_ai_layer_only_through_its_ports() -> None:
    """The one module that may hold an engine is the turn route, and even there
    only via the `AiEngine` protocol."""
    allowed = {"app.ai.ports", "app.ai.engine", "app.ai.disclaimers", "app.ai.scenarios"}
    for path in _modules_in("api"):
        ai_imports = {i for i in _imports(path) if i.startswith("app.ai")}
        unexpected = {i for i in ai_imports if not any(i.startswith(a) for a in allowed)}
        assert not unexpected, f"{path.name} imports {unexpected}"


def test_only_the_provider_constructs_an_engine() -> None:
    """One wiring point. Both implementations are selected in the same file.

    The rule outlived the M4 swap it was written for: with two engines, "which
    one am I talking to" has to have a single answer, and a second construction
    site is how a route ends up pinned to fixtures while `/readyz` reports the
    orchestrated engine.
    """
    for engine in ("FixtureEngine()", "OrchestratedEngine()"):
        home = f"{engine[:-2].lower()}.py".replace("fixtureengine", "fixture_engine").replace(
            "orchestratedengine", "orchestrated_engine"
        )
        constructing = sorted(
            path.relative_to(APP).as_posix()
            for path in APP.rglob("*.py")
            if engine in path.read_text() and path.name != home
        )
        assert constructing == ["ai/engine.py"], (
            f"{engine} is constructed outside its provider: {constructing}"
        )


def test_ai_layer_does_not_import_the_database() -> None:
    """The engine receives a resolved request and returns events. If it could
    read the database it could also violate FR-7 by retrieving a document the
    scope resolver excluded."""
    offenders: list[str] = []
    for path in _modules_in("ai"):
        for imported in _imports(path):
            if imported.startswith(("app.models", "app.db", "sqlalchemy")):
                offenders.append(f"{path.relative_to(APP.parent)} imports {imported}")
    assert not offenders, "the AI layer must not touch the database directly:\n" + "\n".join(
        f"  - {o}" for o in offenders
    )


def test_ai_layer_does_not_import_fastapi() -> None:
    """It must be callable from a test with a literal, not only from a request."""
    offenders: list[str] = []
    for path in _modules_in("ai"):
        for imported in _imports(path):
            if imported.startswith(("fastapi", "starlette")):
                offenders.append(f"{path.relative_to(APP.parent)} imports {imported}")
    assert not offenders, "\n".join(offenders)


def test_fixture_engine_satisfies_the_port() -> None:
    from app.ai.fixture_engine import FixtureEngine
    from app.ai.ports import AiEngine

    engine = FixtureEngine()
    assert isinstance(engine, AiEngine)
    for method in ("answer", "summarize", "draft", "route_intent"):
        assert callable(getattr(engine, method)), f"{method} is missing from the engine"
    for attr in ("name", "prompt_version", "model_version", "index_version"):
        assert getattr(engine, attr), f"{attr} is unset — NFR-12 needs it stamped"


def test_orchestrated_engine_satisfies_the_port() -> None:
    """The same assertions, against the implementation that actually answers.

    Constructed without credentials and without the retrieval extra, which is
    the point: if building the engine loaded a model or opened a client, this
    test could not run in CI, and `/readyz` could not report on it either.
    """
    from app.ai.orchestrated_engine import OrchestratedEngine
    from app.ai.ports import AiEngine

    engine = OrchestratedEngine()
    assert isinstance(engine, AiEngine)
    for method in ("answer", "summarize", "draft", "route_intent"):
        assert callable(getattr(engine, method)), f"{method} is missing from the engine"
    for attr in ("name", "prompt_version", "model_version", "index_version"):
        assert getattr(engine, attr), f"{attr} is unset — NFR-12 needs it stamped"


def test_the_orchestration_layer_does_not_import_the_port() -> None:
    """Direction of the adapter: the engine knows both sides, the workflows
    know only their own.

    A workflow that imported `AiRequest` could not be driven by
    `scripts/try_ask.py` or tested with a literal, and `TurnTrace` would start
    being filled in two places."""
    offenders: list[str] = []
    for path in sorted((APP / "ai" / "orchestration").rglob("*.py")):
        for imported in _runtime_imports(path):
            if imported.startswith("app.ai.ports"):
                offenders.append(f"{path.relative_to(APP)} imports {imported}")
    assert not offenders, (
        "the orchestration layer must not import the port at runtime — the adapter "
        "is app/ai/orchestrated_engine.py:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_services_do_not_import_routes() -> None:
    """Dependency direction: routes depend on services, never the reverse."""
    offenders: list[str] = []
    for path in _modules_in("services"):
        for imported in _imports(path):
            if imported.startswith("app.api.routes"):
                offenders.append(f"{path.name} imports {imported}")
    assert not offenders, "\n".join(offenders)


def test_ingestion_does_not_import_the_ai_layer() -> None:
    """Ingestion is band 7 and the AI layer is band 4. Parsing a document must
    not depend on how answers get written."""
    offenders: list[str] = []
    for path in _modules_in("ingestion"):
        for imported in _imports(path):
            if imported.startswith("app.ai"):
                offenders.append(f"{path.name} imports {imported}")
    assert not offenders, "\n".join(offenders)
