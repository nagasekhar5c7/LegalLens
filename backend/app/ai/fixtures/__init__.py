"""Fixture data for the M3 AI engine.

Ported from `frontend/src/mocks/` so the backend answers with exactly what the
frontend was built and validated against. Nothing under `app/api/` or
`app/services/` may import this package — `tests/test_layering.py` enforces
that, which is how NFR-14 stays true rather than aspirational.
"""
