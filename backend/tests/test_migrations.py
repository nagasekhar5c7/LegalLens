"""The migrations must describe the models.

The suite builds its schema with `create_all` for speed, so without this test a
model change could pass every other test and then fail on a real deployment —
the one place it matters. This closes that gap by running Alembic for real and
diffing the result against the metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]

#: Diff entries that are not real drift. SQLite reports index and server-default
#: differences that mean nothing on a fresh database.
IGNORED_PREFIXES = ("add_index", "remove_index")


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_apply_cleanly_and_match_the_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command

    db_path = tmp_path / "migrated.db"
    sync_url = f"sqlite:///{db_path}"
    # `migrations/env.py` reads the URL from app settings, so point those at the
    # throwaway database rather than the developer's.
    monkeypatch.setenv("LL_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(sync_url), "head")

        engine = create_engine(sync_url)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = [
                entry
                for entry in compare_metadata(context, Base.metadata)
                if not (isinstance(entry, tuple) and str(entry[0]).startswith(IGNORED_PREFIXES))
            ]
        engine.dispose()
    finally:
        get_settings.cache_clear()

    assert diff == [], (
        "the migrations no longer describe the models — run "
        "`uv run alembic revision --autogenerate -m '...'`:\n"
        + "\n".join(f"  - {entry}" for entry in diff)
    )


def test_every_model_table_is_created_by_the_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command
    from sqlalchemy import inspect

    db_path = tmp_path / "tables.db"
    sync_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("LL_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(sync_url), "head")
        engine = create_engine(sync_url)
        created = set(inspect(engine).get_table_names())
        engine.dispose()
    finally:
        get_settings.cache_clear()

    expected = set(Base.metadata.tables)
    assert expected <= created, f"missing tables: {sorted(expected - created)}"
    assert "alembic_version" in created


def test_there_is_a_single_migration_head() -> None:
    """Two heads mean a merge is pending and `upgrade head` is ambiguous."""
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    heads = script.get_heads()
    assert len(heads) == 1, f"multiple migration heads: {heads}"


def test_every_migration_is_reversible() -> None:
    """A migration with no downgrade cannot be rolled back, which M6's
    "deployed with demonstrated rollback" requires."""
    script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
    for revision in script.walk_revisions():
        source = Path(revision.module.__file__ or "").read_text()
        body = source.split("def downgrade()")[-1]
        assert body.strip(), f"{revision.revision} has no downgrade body"
        assert "pass" not in body.split("\n")[1] or "op." in body, (
            f"{revision.revision} has an empty downgrade — rollback would be a no-op"
        )
