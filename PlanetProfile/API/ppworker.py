""" ppworker -- the long-lived JSONL worker harness for the API boundary.

A worker imports PlanetProfile exactly once (paying the heavy SPICE/EOS/config cost),
snapshots a pristine deepcopy of the global Params, then loops reading one JSONL job
per line on stdin and writing progress + a terminal result per line on stdout. Each job
runs in its own working directory (jobdir), so the engine's own file writers drop
artifacts under jobdir/<Body>/ with no cross-job interference. Raw logs go to stderr.

Protocol (one JSON object per line):
  stdin  job:   {"type":"job","id":"<id>","spec":{...request...},"jobdir":"/abs/path"}
  stdin  quit:  {"type":"quit"}
  stdout ready: {"type":"ready","pid":123,"version":"..."}
  stdout prog:  {"type":"progress","id":"<id>","stage":"run","percent":50}
  stdout done:  {"type":"result","id":"<id>","status":"succeeded","summary":{...},"manifest":[...]}
  stdout fail:  {"type":"result","id":"<id>","status":"failed","error":{"code":"...","message":"..."}}

Run as:  python -m PlanetProfile.API.ppworker
This module is import-safe: importing it does not start the loop (only main() does).
"""
import os
import sys
import json
import traceback
from copy import deepcopy


# The pristine JSONL channel. main() rebinds this to the real stdout and then points
# sys.stdout at stderr, so engine print()/banner output can never corrupt the pipe.
_REAL_STDOUT = sys.stdout


def _emit(obj):
    """ Write one JSON object as a line to the real stdout and flush (the wire framing). """
    _REAL_STDOUT.write(json.dumps(obj) + '\n')
    _REAL_STDOUT.flush()


def _run_single(spec, jobdir, jobid, engine, pristineParams, emit):
    """ mode=single: run the forward interior model once and serialize the profile. """
    from PlanetProfile.API import mapper, results as resultsmod

    Params = mapper.apply_run_flags(spec, pristineParams)
    Planet = mapper.build_planet(spec)              # whitelist mapper; never importlib

    os.chdir(jobdir)                                # isolate all engine file writes here
    emit({'type': 'progress', 'id': jobid, 'stage': 'run', 'percent': 5})
    Planet, Params = engine.PlanetProfile(Planet, Params)
    emit({'type': 'progress', 'id': jobid, 'stage': 'write', 'percent': 95})

    manifest = resultsmod.build_manifest(jobdir)
    result = resultsmod.extract_single(Planet, manifest=manifest)
    resultsmod.write_result_json(result, jobdir)

    return {'type': 'result', 'id': jobid,
            'status': 'succeeded' if result['meta']['valid'] else 'invalid',
            'summary': result['summary'],
            'meta': result['meta'],
            'manifest': manifest}


def _run_exploreogram(spec, jobdir, jobid, engine, pristineParams, emit):
    """ mode=exploreogram: run a 2-D interior-structure sweep and serialize the grid.

        Uses the importlib-free engine entry point RunExploreGrid. Grids run single-process
        (nested spawn-Pool inside a worker subprocess is the cross-platform hazard the
        CLAUDE.md flags; the server already runs several concurrent workers). Induction and
        gravity are forced off per cell: an ExploreOgram maps interior structure, the
        induction sweep is the separate inductogram mode, and grid induction extraction
        dereferences None when moments weren't computed.
    """
    from PlanetProfile.API import mapper, results as resultsmod

    Params = mapper.apply_run_flags(spec, pristineParams)   # sets DO_EXPLOREOGRAM from mode
    Params = mapper.apply_explore_params(spec, Params)
    Params.DO_PARALLEL = False
    Params.SKIP_INDUCTION = True
    Params.SKIP_GRAVITY = True
    Params = mapper.neutralize_induction(Params)
    Planet = mapper.build_planet(spec)

    os.chdir(jobdir)
    emit({'type': 'progress', 'id': jobid, 'stage': 'grid', 'percent': 5})
    Exploration, Params = engine.RunExploreGrid(Planet, Params)
    emit({'type': 'progress', 'id': jobid, 'stage': 'write', 'percent': 95})

    manifest = resultsmod.build_manifest(jobdir)
    result = resultsmod.extract_grid(Exploration, Params, manifest=manifest)
    resultsmod.write_result_json(result, jobdir)

    valid = result['meta']['valid']
    return {'type': 'result', 'id': jobid,
            'status': 'succeeded' if valid else 'invalid',
            'summary': {'mode': 'exploreogram', 'nx': result['meta']['nx'],
                        'ny': result['meta']['ny'], 'valid': valid,
                        'zNames': result['meta']['zNames']},
            'meta': result['meta'],
            'manifest': manifest}


def _run_inductogram(spec, jobdir, jobid, engine, pristineParams, emit):
    """ mode=inductogram: run a sigma-type induction sweep (ocean conductivity x thickness)
        and serialize the per-excitation response grid. Uses the importlib-free engine entry
        point RunInductGrid; single-process for the same nested-Pool reason as exploreogram.
    """
    from PlanetProfile.API import mapper, results as resultsmod

    Params = mapper.apply_run_flags(spec, pristineParams)   # sets DO_INDUCTOGRAM from mode
    Params = mapper.apply_induct_params(spec, Params)
    Params.DO_PARALLEL = False
    Planet = mapper.build_planet(spec)

    os.chdir(jobdir)
    emit({'type': 'progress', 'id': jobid, 'stage': 'induction', 'percent': 5})
    InductionResults, Params = engine.RunInductGrid(Planet, Params)
    emit({'type': 'progress', 'id': jobid, 'stage': 'write', 'percent': 95})

    manifest = resultsmod.build_manifest(jobdir)
    result = resultsmod.extract_induct_grid(InductionResults, Params, manifest=manifest)
    resultsmod.write_result_json(result, jobdir)

    return {'type': 'result', 'id': jobid, 'status': 'succeeded',
            'summary': {'mode': 'inductogram', 'nx': result['meta']['nx'],
                        'ny': result['meta']['ny'],
                        'nExcitations': len(result['meta']['excitations'])},
            'meta': result['meta'],
            'manifest': manifest}


def run_job(spec, jobdir, jobid, engine, pristineParams, emit=_emit):
    """ Run a single job in jobdir and return the terminal result dict (also emitted).

        Kept separate from the stdin loop so tests can drive one job directly without
        wiring up pipes. ``engine`` is the imported Main module; ``pristineParams`` is the
        untouched global Params snapshot (never mutated -- each job gets a fresh deepcopy).
    """
    from PlanetProfile.API import mapper, validate

    startCwd = os.getcwd()
    os.makedirs(jobdir, exist_ok=True)
    try:
        # Validate before touching the engine (mirrors the Rust 422 path).
        verrors = validate.validate_request(spec)
        if verrors:
            return {'type': 'result', 'id': jobid, 'status': 'failed',
                    'error': {'code': 'invalid_request', 'message': 'validation failed',
                              'fields': verrors}}

        mode = (spec.get('mode') or 'single').lower()
        if mode == 'single':
            return _run_single(spec, jobdir, jobid, engine, pristineParams, emit)
        if mode == 'exploreogram':
            return _run_exploreogram(spec, jobdir, jobid, engine, pristineParams, emit)
        if mode == 'inductogram':
            return _run_inductogram(spec, jobdir, jobid, engine, pristineParams, emit)
        return {'type': 'result', 'id': jobid, 'status': 'failed',
                'error': {'code': 'unsupported_mode',
                          'message': f'mode "{mode}" is not handled by the worker '
                                     '(supported: single, exploreogram, inductogram)'}}
    except mapper.MappingError as e:
        return {'type': 'result', 'id': jobid, 'status': 'failed',
                'error': {'code': 'mapping_error', 'message': 'request references disallowed attributes',
                          'fields': e.errors}}
    except Exception as e:                          # noqa: BLE001 -- report, don't crash the worker
        traceback.print_exc()
        return {'type': 'result', 'id': jobid, 'status': 'failed',
                'error': {'code': 'engine_error', 'message': str(e),
                          'traceback': traceback.format_exc()}}
    finally:
        os.chdir(startCwd)


def main():
    # Keep the real stdout as the JSONL channel and send everything the engine prints
    # (banner, LaTeX warning, logs) to stderr so the pipe carries only our JSON lines.
    global _REAL_STDOUT
    _REAL_STDOUT = sys.stdout
    sys.stdout = sys.stderr

    # Heavy one-time import; stderr so it never pollutes the stdout JSONL channel.
    print('ppworker: importing PlanetProfile engine...', file=sys.stderr, flush=True)
    from PlanetProfile import Main as engine
    from PlanetProfile.GetConfig import Params as configParams
    try:
        from PlanetProfile.Utilities.PPversion import ppVerNum as version
    except Exception:
        version = 'unknown'

    pristineParams = deepcopy(configParams)         # snapshot; jobs deepcopy from this
    _emit({'type': 'ready', 'pid': os.getpid(), 'version': str(version)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _emit({'type': 'result', 'id': None, 'status': 'failed',
                   'error': {'code': 'bad_json', 'message': str(e)}})
            continue
        mtype = msg.get('type')
        if mtype == 'quit':
            break
        if mtype != 'job':
            _emit({'type': 'result', 'id': msg.get('id'), 'status': 'failed',
                   'error': {'code': 'bad_message', 'message': f'unexpected message type "{mtype}"'}})
            continue
        jobid = msg.get('id')
        spec = msg.get('spec') or {}
        jobdir = msg.get('jobdir') or os.path.join(os.getcwd(), f'job_{jobid}')
        _emit(run_job(spec, jobdir, jobid, engine, pristineParams))


if __name__ == '__main__':
    main()
