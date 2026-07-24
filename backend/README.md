# backend/ — Local Rust orchestration server (planned)

This directory will hold the local-only Rust server that fronts the Python
PlanetProfile engine over an HTTP/JSON API: request validation, an async job
queue, progress streaming, and per-job working directories. It **never**
reimplements the physics — it drives the Python engine as managed worker
processes.

Nothing here yet; this is scaffolding for Phase 4 of the refactor. See the full
design in [`docs/spec/MoonMelodies_Spec_and_Refactor.md`](../docs/spec/MoonMelodies_Spec_and_Refactor.md)
— Section 3 (architecture contract) and Section 5 (backend spec).
