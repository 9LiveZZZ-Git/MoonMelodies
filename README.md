# MoonMelodies
![MoonMelodies logo](assets/brand/PPlogoDocs.png)

**MoonMelodies is a fork of [PlanetProfile](https://github.com/vancesteven/PlanetProfile)** — the open-source framework for building 1D interior-structure models of icy moons and ocean worlds. MoonMelodies keeps PlanetProfile's scientific engine **unchanged** and builds an interactive, browser-based tool around it: it reorganizes and slims the repository, hardens the engine for headless/server use, and adds a declarative JSON API so the physics can be driven by a local backend and a static web frontend instead of by hand-edited Python files.

The full plan and design live in [`docs/spec/MoonMelodies_Spec_and_Refactor.md`](docs/spec/MoonMelodies_Spec_and_Refactor.md).

> **The import package is still `PlanetProfile`.** `from PlanetProfile... import ...` and `python -m PlanetProfile.*` are unchanged; only the installed *distribution* is named `MoonMelodies`. This keeps every model file, script, and downstream import working exactly as upstream.

---

## Credit — the science is PlanetProfile's

MoonMelodies is a wrapper-and-tooling fork. **All of the interior-structure physics is PlanetProfile**, created and maintained by Steven D. Vance and collaborators. If you use MoonMelodies for research, please credit PlanetProfile:

- Upstream repository: <https://github.com/vancesteven/PlanetProfile> (NASA mirror: <https://github.com/NASA-Planetary-Science/PlanetProfile>; docs: <https://vancesteven.github.io/PlanetProfile>)
- Suggested acknowledgement: *"Data used in this work were generated using the open-source PlanetProfile software hosted on GitHub (https://github.com/vancesteven/PlanetProfile)."*
- Please cite:
  - Vance et al. (2018), *Geophysical investigations of habitability in ice-covered ocean worlds*, JGR: Planets, [10.1002/2017JE005341](https://doi.org/10.1002/2017JE005341).
  - Styczinski, Vance, and Melwani Daswani (2023), *PlanetProfile: Self-consistent interior structure modeling for ocean worlds and rocky dwarf planets in Python*, Earth and Space Science, 10(8), [10.1029/2022EA002748](https://doi.org/10.1029/2022EA002748).

We'd also love to hear about your work — reach the PlanetProfile team at steven.d.vance@jpl.nasa.gov.

---

## What MoonMelodies adds

Relative to upstream PlanetProfile, this fork contributes:

- **A declarative JSON API boundary** (`PlanetProfile/API/`). A whitelist mapper builds the engine's `PlanetStruct` from plain JSON — never by importing a user `PP<Body>.py` file — and a thin `ppworker` harness runs models over stdin/stdout, so the engine can be driven safely by a server or UI. Verified to reproduce a CLI run bit-for-bit.
- **A local backend** (`PlanetProfile/API/server/`, FastAPI + uvicorn). A loopback-only HTTP/JSON + SSE server that fronts the engine through a warm pool of `ppworker` subprocesses — submit a model as JSON, watch progress over Server-Sent Events, and pull back results + artifacts. Loopback-bound with a per-session token, CORS/PNA handling for a GitHub-Pages frontend, and one job per worker (the engine is non-reentrant). Beyond single models it also serves **2-D ExploreOgram grid sweeps** and **amortized Bayesian inference** (`/infer`, sampling the downloaded SBI posteriors). See [Running the local backend](#running-the-local-backend).
- **A browser frontend** (`frontend/index.html`). A single self-contained page — no build step, no external assets — that drives the backend and renders results as an engineering cross-section "plate": a to-scale radial interior diagram plus the full figure suite (structure, thermodynamics, seismic, electrical, **tidal Love numbers**, **magnetic-induction response**), a **2-D sweep heatmap** (ExploreOgram, with contours), and a **posterior corner plot** for Bayesian inference. It talks to the loopback backend with a session token; the backend can serve it same-origin via `--static-dir frontend`.
- **LaTeX-free plotting.** Every figure renders through matplotlib's built-in mathtext, so no LaTeX/siunitx installation is needed for headless or server-side plot generation.
- **Bayesian interior inference** (`PlanetProfile.Inference`). MCMC (pocoMC) and simulation-based inference (`sbi`/`torch`) constrain interior parameters against tidal Love numbers, gravity, and magnetic-induction observables, with a full suite of posterior and diagnostic figures. Optional — `pip install -e ".[inference]"`.
- **A stabilized engine.** Imports cleanly on modern Python, is safe to call repeatedly within one long-lived process (per-run config isolation), no longer blocks on import-time stdin prompts, and ships with a regression-test suite (`tests/`). The full `BuildTest` physics suite runs green.
- **A much smaller repository.** The frozen MATLAB implementation is archived under `legacy-matlab/`, large binary data was purged from git history (`.git` shrank ~94%), and the Perple_X EOS tables are fetched on install rather than committed.

The backend (`PlanetProfile/API/server/`) and the browser frontend (`frontend/index.html`) are both built; point the server at the page with `--static-dir frontend` and open the printed loopback URL. **In progress (see the spec):** the InductOgram σ×D induction-sweep mode (the ExploreOgram interior sweep is wired end-to-end) and packaging the page for static GitHub-Pages hosting against a user's local backend.

---

## The engine (PlanetProfile)

PlanetProfile constructs 1D interior-structure models from a body's bulk properties, using self-consistent thermodynamics for fluid, rock, and mineral phases, and derives sound speeds, seismic attenuation, electrical conductivity, magnetic-induction responses, tidal Love numbers, and gravity. A model is defined by an input `PP<Body>.py` file. Capabilities:

- **Self-consistent ocean-world modeling** — geophysics coupled to thermodynamic and transport properties set by the ocean geochemistry:
  - Laboratory-measured compositions: pure water and NaCl (SeaFreeze), seawater (GSW/TEOS-10), MgSO₄ (Vance tables).
  - Arbitrary compositions via the Frezchem/Supcrt geochemical databases in the Gibbs-minimization package Reaktoro.
- **Self-consistent interior modeling** — silicate and core geophysics coupled to material equations of state (CV, CM, …) from Perple_X.
- **Tidal Love numbers** via PyALMA3.
- **Spherical-harmonic and asymmetric magnetic-induction** responses via MoonMag.
- **Large-scale explorations** across two parameters (ExploreOgram/InductOgram) or many models via Monte Carlo.
- **Exports** to `.txt`, `.pkl`, and `.mat`, plus built-in plots.

The engine's own change history is in [CHANGELOG.md](CHANGELOG.md).

---

## Getting started

MoonMelodies is developed from a clone (it is **not** published to PyPI). The scientific stack has heavyweight compiled dependencies, so a conda/mamba environment is strongly recommended.

```bash
# 1. Create an environment with the native scientific dependencies
mamba create -n moonmelodies python=3.11
mamba activate moonmelodies
mamba install -c conda-forge numpy scipy matplotlib mpmath pandas gsw spiceypy cmasher reaktoro obspy
pip install SeaFreeze hdf5storage PyALMA3

# 2. Clone and install this fork (editable)
git clone https://github.com/9LiveZZZ-Git/MoonMelodies
cd MoonMelodies
pip install -e .                       # add ".[backend]" for the local server, ".[inference]" for Bayesian inference

# 3. One-time engine setup: seeds UserConfigs/ and downloads the ~164 MB Perple_X EOS tables
python -m PlanetProfile.install PPinstall

# 4. Run a model
python PlanetProfileCLI.py Europa                 # by body name
python PlanetProfileCLI.py path/to/PPBody.py      # by input file
```

Or from Python:

```python
from PlanetProfile.Main import RunPPfile
RunPPfile('Europa', 'PPEuropa.py')
```

Exact dependency pins are in [`pyproject.toml`](pyproject.toml). See the prerequisite links below for anything conda/pip can't resolve directly.

> **What a fresh clone + install covers.** The steps above give a **complete engine and backend** — every model type (hydrosphere, interior, seismic, gravity/Love numbers, magnetic induction, ExploreOgram/InductOgram/MonteCarlo) runs, because the SPICE leap-second/PCK/frame kernels, Reaktoro databases, all `Default/PP<Body>.py` inputs, and the MgSO₄/NH₃ reference data are tracked in the repo, and `install` downloads the ~164 MB Perple_X EOS tables and seeds `UserConfigs/`. Two **optional** features need data that is *not* in the repo and must be fetched separately: SPICE **ephemeris kernels** (`.bsp`, large — only for spacecraft trajectory analysis; download from [NAIF](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/) into `SPICE/`), and the **Bayesian-inference** structure caches + SBI model artifacts (see the spec's inference notes). Neither is required for standard modeling or the backend.

### Running the tests

```bash
python -m PlanetProfile.BuildTest        # full physics suite (all Test/PPTest*.py bodies)
python -m PlanetProfile.BuildTest full 5 # a single test profile
pytest tests/                            # fast regression tests (optional)
```

Adding major functionality should come with a matching `PlanetProfile/Test/PPTest#.py` body; `BuildTest` must pass before merging.

---

## Running the local backend

The backend fronts the engine over HTTP so a browser UI (or `curl`) can drive it. Install the backend extra and start it from a directory seeded by `python -m PlanetProfile.install` (which creates `UserConfigs/`):

```bash
pip install -e ".[backend]"          # FastAPI + uvicorn, on top of the engine
python -m PlanetProfile.install      # once: seed UserConfigs/ + download the EOS tables
python -m PlanetProfile.API.server --workers 4 --allowed-origin https://<user>.github.io
```

On startup it prints the loopback URL and a random **session token**; every request needs it (`Authorization: Bearer <token>`, or `?token=` for the SSE stream). Key endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness + worker/EOS-cache readiness (unauthenticated, so the frontend can detect it) |
| `GET /bodies` · `/schema` · `/schema/{body}` | body list, input/output schema + enums, per-body default inputs |
| `POST /runs` | submit a model as JSON → `202 {id}` — `mode:"single"` or `mode:"exploreogram"` (2-D grid sweep); `422` with field-level errors |
| `GET /runs/{id}/events` | Server-Sent Events progress stream |
| `GET /runs/{id}` · `/result` · `/artifacts` · `/artifacts/{name}` | status, result JSON, artifact manifest + download |
| `DELETE /runs/{id}` | cancel (kills + respawns the worker) |
| `GET /infer/artifacts` · `/infer/artifacts/{id}` | list the downloaded SBI posteriors + per-artifact observables, priors, and deploy guards |
| `POST /infer` | condition a posterior on observables → posterior samples + summary (amortized; sub-second) |

It binds `127.0.0.1`/`::1` **only**, runs one job per warm worker (the engine is non-reentrant), and validates every request up front. To use the browser UI, serve it same-origin (the zero-CORS path) and open the printed URL:

```bash
python -m PlanetProfile.API.server --workers 4 --static-dir frontend
# → open http://127.0.0.1:8787/ and paste the session token when prompted
```

See [`docs/spec`](docs/spec/MoonMelodies_Spec_and_Refactor.md) §3/§5 for the full contract.

**Robustness & limits.** The backend has been audited across sanity, memory, speed, and robustness: a killed worker is respawned and the pool keeps serving, cancellation and timeouts kill+respawn cleanly, and malformed / oversized / unauthenticated / path-traversal requests are rejected with the right status while the server stays up (server RSS stays flat across jobs). Tunable safeguards: `--workers` (default `cpu−2`), `--job-timeout` (per-job wall-clock → kill+respawn), plus internal caps on request-body size and grid cells, a job-retention reaper + count cap, and **worker recycling** (a worker is respawned after a set number of jobs to bound the engine's per-run memory growth in a long-lived process).

## Prerequisites (the engine's scientific stack)

Most of these install via the commands above; the links are for manual installs and background.

- **SeaFreeze** — <https://github.com/Bjournaux/SeaFreeze> (`pip install SeaFreeze`)
- **Gibbs Seawater (TEOS-10)** — <https://www.teos-10.org/> (`conda install -c conda-forge gsw`)
- **Perple_X** — <http://www.perplex.ethz.ch/> — outputs are downloaded on install into the platform cache (not committed to git).
- **Reaktoro** — <https://reaktoro.org> (`conda install -c conda-forge reaktoro`)
- **PyALMA3** — <https://github.com/drsaikirant88/PyALMA3> (`pip install PyALMA3`)
- **spiceypy** (NAIF CSPICE) — installed via conda-forge; SPICE kernels ship with the package.
- **TauP/ObsPy** (optional, seismic) — <https://www.seis.sc.edu/taup/> (`conda install -c conda-forge obspy`)
- A LaTeX distribution is **not required** — MoonMelodies renders labels with matplotlib mathtext. LaTeX is used only if it is installed.

> **Parallelism.** Some calculations use Python `multiprocessing`. If you hit cross-platform issues, disable it with `Params.DO_PARALLEL = False` in `UserConfigs/configPP.py`.

---

## Repository layout

| Path | What it is |
|---|---|
| `PlanetProfile/` | The scientific engine (the import package — unchanged from upstream in name and layout). |
| `PlanetProfile/API/` | The MoonMelodies JSON API boundary (mapper, validation, schema, results, `ppworker`) + the worker `pool` and the FastAPI `server/`. |
| `PlanetProfile/Inference/` | Bayesian interior inference (MCMC + SBI). |
| `legacy-matlab/` | The frozen MATLAB implementation, archived (imported by nothing in Python). |
| `frontend/index.html` | The browser UI — a single self-contained page (inline CSS/JS, no build step) that drives the backend and renders the interior plate, figure suite, ExploreOgram heatmap, and inference corner plots. |
| `docs/spec/` | The MoonMelodies spec & refactor plan. |
| `tests/` | Regression tests. |
| `configs/`, `data-assets/` | Configuration and data-manifest scaffolding. |

### Legacy MATLAB

The MATLAB implementation is **frozen** and lives under [`legacy-matlab/`](legacy-matlab/) (`config.m`, `PlanetProfile.m`, per-body `PP<Body>.m`, `make install`). It is not maintained going forward; new development is Python-only in `PlanetProfile/`.

---

## Contributing & license

MoonMelodies is open-source under the same license as PlanetProfile — see [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests for the fork's tooling (API, backend, frontend, packaging) go to <https://github.com/9LiveZZZ-Git/MoonMelodies>. **Improvements to the underlying physics belong upstream** — please contribute those to [PlanetProfile](https://github.com/vancesteven/PlanetProfile) so the whole community benefits. Community guidelines are in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
