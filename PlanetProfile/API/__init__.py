""" MoonMelodies engine API boundary (Phase 3).

This subpackage draws a declarative JSON contract over the PlanetProfile engine so
an external orchestrator (the Phase 4 Rust backend) can drive model runs without
importing user ``PP<Body>.py`` files or executing arbitrary code. Nothing here
changes the physics; it only maps validated JSON onto a ``PlanetStruct``, runs the
existing pipeline, and serializes the outputs.

Modules:
    mapper    -- whitelist JSON <-> PlanetStruct mapper and run-flag application
    validate  -- up-front request validation (two-of-three rule, enums, allowlists)
    results   -- PlanetStruct -> result.json (+ manifest) serialization
    schema    -- the /schema payload: input JSON Schema, output dictionary, enums
    ppworker  -- the long-lived JSONL stdin/stdout worker harness
"""

from PlanetProfile.API.mapper import (
    build_planet, apply_run_flags, planet_to_spec, MappingError, SECTIONS,
)
from PlanetProfile.API.validate import validate_request, OCEAN_COMPS, EXPLORE_NAMES
from PlanetProfile.API.results import (
    extract_single, build_manifest, to_jsonable, write_result_json, PPJSONEncoder,
)
