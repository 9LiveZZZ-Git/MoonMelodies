""" Whitelist JSON <-> PlanetStruct mapper for the API boundary.

``build_planet`` constructs a ``PlanetStruct`` programmatically from a declarative
JSON request, and is a drop-in replacement for the ``importlib.import_module(...).Planet``
line the CLI uses -- but it NEVER imports a user file, so a request cannot introduce
new code paths or execute arbitrary code (the security property the spec requires).

The whitelist is derived from the structs themselves: a section key may set an
attribute only if that attribute already exists on a freshly constructed substruct
AND its default value is a JSON-compatible type (scalar / list / str / bool / None /
ndarray / dict-of-scalars). Custom class instances (EOS objects, ConstantPropsStruct,
ClathDissoc, ALMA models) are therefore rejected automatically, plus an explicit
name blocklist covers None-defaulted object holders that the type test alone can't see.
"""
from copy import deepcopy
import numpy as np

from PlanetProfile.Utilities.defineStructs import PlanetStruct

# JSON request section -> Planet substruct attribute name.
SECTIONS = {
    'bulk': 'Bulk',
    'do': 'Do',
    'steps': 'Steps',
    'ocean': 'Ocean',
    'sil': 'Sil',
    'core': 'Core',
    'seismic': 'Seismic',
    'magnetic': 'Magnetic',
    'gravity': 'Gravity',
}

# Top-level Planet scalars a request may set (GetPfreeze / GetTfreeze search bounds).
_TOPLEVEL = frozenset({
    'PfreezeLower_MPa', 'PfreezeUpper_MPa', 'PfreezeRes_MPa',
    'TfreezeLower_K', 'TfreezeUpper_K', 'TfreezeRes_K',
})

# Attribute names that are never settable from JSON even when their default is None
# or an (empty) dict: these hold EOS objects, interpolators, and ALMA solution state.
_BLOCK_NAMES = frozenset({
    'EOS', 'meltEOS', 'surfIceEOS', 'iceEOS', 'poreEOS', 'ClathDissoc',
    'model', 'ALMAModel', 'y', 'y1', 'pyAlmaParams', 'Reduced',
})

_JSONISH = (bool, int, float, str, list)


class MappingError(ValueError):
    """ Raised when a request references an attribute outside the whitelist. Carries
        a list of {field, message} dicts so the caller can return field-level errors. """
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(f"{e['field']}: {e['message']}" for e in self.errors))


def _is_settable(name, current):
    """ Whether an attribute with the given fresh default value may be set from JSON. """
    if name in _BLOCK_NAMES:
        return False
    if current is None:
        return True
    if isinstance(current, _JSONISH):
        return True
    if isinstance(current, dict):
        # dict-of-scalars (e.g. Do.ConstantProps, Ocean.phiMax_frac); merged by sub-key.
        return all(not hasattr(v, '__dict__') for v in current.values())
    if isinstance(current, np.ndarray):
        return True
    if isinstance(current, (np.floating, np.integer, np.bool_)):
        return True
    return False  # custom class instance -> blocked


def _assign(target, key, value, path, errors):
    """ Set target.key = value if whitelisted, else record a field-level error. """
    if not hasattr(target, key):
        errors.append({'field': path, 'message': 'unknown attribute (not in whitelist)'})
        return
    current = getattr(target, key)
    if not _is_settable(key, current):
        errors.append({'field': path, 'message': 'attribute is not settable via the API'})
        return
    if isinstance(current, dict) and current:
        # Merge onto the existing dict, rejecting sub-keys that don't already exist.
        if not isinstance(value, dict):
            errors.append({'field': path, 'message': 'expected an object of sub-keys'})
            return
        merged = deepcopy(current)
        for sk, sv in value.items():
            if sk not in merged:
                errors.append({'field': f'{path}.{sk}', 'message': 'unknown key'})
                continue
            merged[sk] = sv
        setattr(target, key, merged)
    else:
        setattr(target, key, value)


def build_planet(spec, strict=True):
    """ Build a PlanetStruct from a declarative JSON request dict.

        spec['body'] selects the body name; the section dicts (bulk/do/ocean/sil/core/
        steps/seismic/magnetic/gravity) set whitelisted substruct attributes. Top-level
        Planet search-bound scalars may appear under spec['planet'].

        Raises MappingError (when strict) if the request references any attribute outside
        the whitelist; otherwise unknown attributes are silently skipped.
    """
    body = spec.get('body')
    if not body or not isinstance(body, str):
        raise MappingError([{'field': 'body', 'message': 'a body name string is required'}])
    Planet = PlanetStruct(body)

    errors = []
    for section, attrName in SECTIONS.items():
        block = spec.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            errors.append({'field': section, 'message': 'expected an object'})
            continue
        substruct = getattr(Planet, attrName)
        for key, value in block.items():
            _assign(substruct, key, value, f'{section}.{key}', errors)

    planetTop = spec.get('planet')
    if isinstance(planetTop, dict):
        for key, value in planetTop.items():
            if key in _TOPLEVEL:
                setattr(Planet, key, value)
            else:
                errors.append({'field': f'planet.{key}', 'message': 'unknown attribute (not in whitelist)'})

    if errors and strict:
        raise MappingError(errors)
    return Planet


def apply_run_flags(spec, Params):
    """ Return a deepcopy of Params with this request's run/mode flags applied.

        Defaults are geared to a headless API run: compute fresh, plots OFF (client-side
        replot), and write profile artifacts into the jobdir. ``run`` keys are optional.
    """
    Params = deepcopy(Params)
    run = spec.get('run') or {}
    mode = (spec.get('mode') or 'single').lower()

    Params.CALC_NEW = bool(run.get('calcNew', True))
    Params.SKIP_PLOTS = bool(run.get('skipPlots', True))          # default: no server-side plotting
    Params.CALC_SEISMIC = bool(run.get('calcSeismic', True))
    Params.CALC_CONDUCT = bool(run.get('calcConduct', True))
    Params.CALC_VISCOSITY = bool(run.get('calcViscosity', True))
    Params.CALC_OCEAN_PROPS = bool(run.get('calcOceanProps', True))
    Params.SKIP_INDUCTION = not bool(run.get('calcInduction', True))
    Params.SKIP_GRAVITY = not bool(run.get('calcGravity', True))
    Params.NO_SAVEFILE = bool(run.get('noSaveFile', False))       # default: keep files as artifacts
    Params.SAVE_AS_MATLAB = bool(run.get('saveMatlab', False))
    Params.DO_PARALLEL = bool(run.get('parallel', True))

    # A single run must never take a grid or in-progress branch inside the pipeline.
    Params.DO_EXPLOREOGRAM = mode == 'exploreogram'
    Params.DO_INDUCTOGRAM = mode == 'inductogram'
    Params.DO_MONTECARLO = mode == 'montecarlo'
    Params.INVERSION_IN_PROGRESS = False
    Params.MONTECARLO_IN_PROGRESS = False
    Params.INDUCTOGRAM_IN_PROGRESS = False
    Params.EXPLOREOGRAM_IN_PROGRESS = False
    return Params


# ---------------------------------------------------------------------------
# Build-time helper: turn an (un-run) Planet into a minimal request spec. Used to
# generate the curated /schema/{body} defaults from a PP<Body>.py Planet WITHOUT
# shipping the file, and to round-trip test the mapper. Not part of request handling.
# ---------------------------------------------------------------------------

def _jsonish_value(v):
    """ Return a JSON-serializable copy of v, or None if v is not a plain input value. """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (list, tuple)):
        out = [_jsonish_value(x) for x in v]
        return out if all(x is not None or v[i] is None for i, x in enumerate(out)) else None
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, dict):
        if any(hasattr(x, '__dict__') for x in v.values()):
            return None
        return {k: _jsonish_value(x) for k, x in v.items()}
    return None


def _equal(a, b):
    """ Equality that treats NaN as equal to NaN (so NaN-defaulted derived attributes
        are recognized as 'unchanged' and left out of an extracted spec). Recurses into
        lists and dicts. """
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (a != a and b != b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


def planet_to_spec(Planet, includeDefaults=False):
    """ Extract a declarative request spec from a Planet object (before it is run).

        Captures each whitelisted, JSON-serializable attribute that differs from a
        freshly constructed substruct default (or all of them when includeDefaults),
        producing a spec that ``build_planet`` reconstructs faithfully.
    """
    fresh = PlanetStruct(Planet.name)
    spec = {'body': Planet.name}
    for section, attrName in SECTIONS.items():
        src = getattr(Planet, attrName)
        ref = getattr(fresh, attrName)
        block = {}
        for key, current in vars(ref).items():
            if not _is_settable(key, current):
                continue
            val = _jsonish_value(getattr(src, key, None))
            if val is None and getattr(src, key, None) is not None:
                continue  # unserializable actual value -> skip
            refVal = _jsonish_value(current)
            if includeDefaults or not _equal(val, refVal):
                block[key] = val
        if block:
            spec[section] = block

    # Top-level Planet scalars (GetPfreeze/GetTfreeze search bounds). These are NOT on a
    # substruct, so they are captured separately -- omitting them silently falls back to
    # the default search range, which shifts an iterative solve at the ~1e-13 level.
    planetTop = {}
    for key in _TOPLEVEL:
        cur = getattr(fresh, key, None)
        val = _jsonish_value(getattr(Planet, key, None))
        if val is None and getattr(Planet, key, None) is not None:
            continue
        if includeDefaults or not _equal(val, _jsonish_value(cur)):
            planetTop[key] = val
    if planetTop:
        spec['planet'] = planetTop
    return spec
