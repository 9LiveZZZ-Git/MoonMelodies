# tests/ — Thin top-level test layer

A place for repository-level tests that do not belong inside the import package.
The engine's own regression suite stays where `BuildTest.py` expects it
(`PlanetProfile/Test/PPTest*.py`) and must not move; the wrappers here shell out
to it and (later) exercise the JSON API contract.

- `test_buildtest.py` — smoke test that runs the full `python -m PlanetProfile.BuildTest`.
  Requires the scientific stack (numpy, scipy, SeaFreeze, gsw, spiceypy, …) and
  is slow (minutes); intended as a CI gate, not a fast unit test.
