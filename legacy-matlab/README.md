# legacy-matlab/ — Frozen MATLAB implementation (archived)

This is the original MATLAB implementation of PlanetProfile, archived out of the
repository root during the Phase 1 cleanup. It is **frozen and unmaintained** —
new development happens only in the Python package at `PlanetProfile/`.

Contents (moved verbatim from the repo root, history preserved via `git mv`):
- `PlanetProfile.m`, `config.m`, `PPTest.m`, `makefile` — MATLAB entry points and build.
- `Thermodynamics/`, `Utilities/`, `MagneticInduction/`, `SPICE/`, `Comparison/` — MATLAB sources and data.
- `bodies/<Body>/` — the per-body MATLAB input dirs (`PP<Body>.m`).

Nothing here is imported by the Python engine. The MATLAB setup instructions in
the top-level `README.md` refer to paths that now live under this directory.
