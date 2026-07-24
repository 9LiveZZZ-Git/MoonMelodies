# frontend/ — Static web UI for GitHub Pages (planned)

This directory will hold the static single-page app that drives the local Rust
backend: body/parameter forms mapped from `PlanetStruct`, run submission with
live progress, and result views (profiles, layer tables, induction, gravity).

Nothing here yet; this is scaffolding for Phase 6 of the refactor. Note the
browser mixed-content constraint (an HTTPS Pages origin cannot call
`http://localhost`) — see the design in
[`docs/spec/MoonMelodies_Spec_and_Refactor.md`](../docs/spec/MoonMelodies_Spec_and_Refactor.md),
Section 4 (UI) and Section 6 (frontend/hosting).
