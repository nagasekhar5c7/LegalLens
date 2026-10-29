#!/usr/bin/env python
"""Write `openapi.json` from the live app.

Committed so the frontend has a reviewable contract artefact and so a change to
the wire shape shows up as a diff rather than as a surprise at integration.

    uv run python scripts/export_openapi.py          # write it
    uv run python scripts/export_openapi.py --check   # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
TARGET = BACKEND / "openapi.json"

# Runnable as a plain script from anywhere, not only via `python -m`.
sys.path.insert(0, str(BACKEND))


def build() -> str:
    from app.start import create_app

    spec = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed file is out of date",
    )
    args = parser.parse_args()

    generated = build()

    if args.check:
        if not TARGET.exists():
            print(f"{TARGET.name} is missing — run: make openapi", file=sys.stderr)
            return 1
        if TARGET.read_text() != generated:
            print(
                f"{TARGET.name} is out of date — run: make openapi",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.name} is current")
        return 0

    TARGET.write_text(generated)
    spec = json.loads(generated)
    print(f"wrote {TARGET.relative_to(BACKEND)} — {len(spec['paths'])} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
