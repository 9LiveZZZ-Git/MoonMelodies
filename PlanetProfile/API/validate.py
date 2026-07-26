""" Up-front validation of an API run request.

These are the checks the spec requires *before* a worker is touched (the Rust layer
will call the same rules to answer HTTP 422 with field-level errors). Every function
returns a list of {field, message} dicts -- empty means valid. Kept dependency-free
(pure Python + a directory scan) so it can run anywhere, including in tests.
"""
import os

# The 19 default bodies (Default/<Body>/), plus the Test harness family.
KNOWN_BODIES = frozenset({
    'Ariel', 'Callisto', 'Dione', 'Enceladus', 'Europa', 'Ganymede', 'Iapetus', 'Io',
    'Luna', 'Mimas', 'Miranda', 'Oberon', 'Pluto', 'Rhea', 'Tethys', 'Titan',
    'Titania', 'Triton', 'Umbriel',
})

# Ocean compositions the EOS router understands; CustomSolution* is prefix-matched.
OCEAN_COMPS = frozenset({'Seawater', 'MgSO4', 'PureH2O', 'NH3', 'NaCl', 'none'})

# The three quantities constrained by the two-of-three rule (real engine attr names).
_TWO_OF_THREE = (('bulk', 'Tb_K'), ('bulk', 'zb_approximate_km'), ('ocean', 'wOcean_ppt'))

# ExploreOgram / InductOgram axis names commonly swept (extendable; used for the enum
# check and the /schema payload). Not exhaustive of engine internals, but the safe set.
EXPLORE_NAMES = frozenset({
    'wOcean_ppt', 'Tb_K', 'zb_approximate_km', 'Htidal_Wm3', 'Qrad_Wkg', 'xFeS',
    'rhoSilInput_kgm3', 'silPhi_frac', 'icePhi_frac', 'Cmeasured', 'ionosTop_km',
    'sigmaIonos_Sm', 'R_m', 'oceanComp',
})
EXPLORE_Z_NAMES = frozenset({
    'D_km', 'zb_km', 'CMR2mean', 'Rcore_km', 'rhoOceanMean_kgm3', 'sigmaMean_Sm',
    'Tmean_K', 'kLoveAmp', 'hLoveAmp', 'Pseafloor_MPa', 'qSurf_Wm2',
})

GRID_MODES = frozenset({'exploreogram', 'inductogram', 'montecarlo'})


def _is_set(spec, section, key):
    block = spec.get(section) or {}
    return block.get(key, None) is not None


def known_eos_tables():
    """ Discover the allowed silicate/core EOS table filenames (bare basenames). Returns
        None if the table directory can't be found, meaning 'skip membership check'. """
    here = os.path.dirname(os.path.abspath(__file__))
    tabledir = os.path.normpath(os.path.join(here, '..', 'Thermodynamics', 'EOStables'))
    if not os.path.isdir(tabledir):
        return None
    names = set()
    for root, _dirs, files in os.walk(tabledir):
        for fn in files:
            if fn.lower().endswith(('.tab', '.mat', '.dat')):
                names.add(fn)
    return names or None


def _validate_eos_field(spec, section, key, allowed, errors):
    block = spec.get(section) or {}
    val = block.get(key)
    if val is None:
        return
    if not isinstance(val, str):
        errors.append({'field': f'{section}.{key}', 'message': 'must be a table filename string'})
        return
    # Path-traversal defense: table names are bare basenames (they become loadmat/loadtxt paths).
    if os.path.basename(val) != val or '..' in val or val != val.strip():
        errors.append({'field': f'{section}.{key}', 'message': 'must be a bare filename (no path separators)'})
        return
    if allowed is not None and val not in allowed:
        errors.append({'field': f'{section}.{key}', 'message': f'unknown EOS table "{val}"'})


def validate_request(spec, eosTables='discover'):
    """ Validate an API run request. Returns a list of {field, message} (empty = OK). """
    errors = []
    if not isinstance(spec, dict):
        return [{'field': '', 'message': 'request must be a JSON object'}]

    # 5. Body enum.
    body = spec.get('body')
    if not body or not isinstance(body, str):
        errors.append({'field': 'body', 'message': 'a body name is required'})
    elif body not in KNOWN_BODIES and not body.startswith('Test'):
        errors.append({'field': 'body', 'message': f'unknown body "{body}"'})

    # Mode.
    mode = (spec.get('mode') or 'single').lower()
    if mode not in ({'single'} | GRID_MODES):
        errors.append({'field': 'mode', 'message': f'unknown mode "{mode}"'})

    do = spec.get('do') or {}
    noH2O = bool(do.get('NO_H2O', False))

    # 3. Ocean composition enum.
    comp = (spec.get('ocean') or {}).get('comp')
    if comp is not None and comp not in OCEAN_COMPS and not str(comp).startswith('CustomSolution'):
        errors.append({'field': 'ocean.comp', 'message': f'unknown ocean composition "{comp}"'})

    # 1. Two-of-three (only meaningful when surface H2O is present).
    if not noH2O:
        nSet = sum(_is_set(spec, sec, key) for sec, key in _TWO_OF_THREE)
        if nSet != 2:
            names = ', '.join(f'{s}.{k}' for s, k in _TWO_OF_THREE)
            errors.append({'field': 'bulk/ocean',
                           'message': f'exactly two of ({names}) must be set for a body with H2O; {nSet} set'})
    else:
        # 2. NO_H2O requires a surface heat flux.
        if not _is_set(spec, 'bulk', 'qSurf_Wm2'):
            errors.append({'field': 'bulk.qSurf_Wm2', 'message': 'required when do.NO_H2O is true'})

    # 4. Explore/grid axis enums.
    if mode in GRID_MODES:
        ex = spec.get('explore')
        if not isinstance(ex, dict):
            errors.append({'field': 'explore', 'message': f'required for mode "{mode}"'})
        else:
            for axis in ('xName', 'yName'):
                nm = ex.get(axis)
                if nm is None:
                    errors.append({'field': f'explore.{axis}', 'message': f'required for mode "{mode}"'})
                elif nm not in EXPLORE_NAMES:
                    errors.append({'field': f'explore.{axis}', 'message': f'unknown sweep parameter "{nm}"'})
            zn = ex.get('zName')
            if zn is not None:
                zlist = zn if isinstance(zn, list) else [zn]
                for z in zlist:
                    if z not in EXPLORE_Z_NAMES:
                        errors.append({'field': 'explore.zName', 'message': f'unknown output "{z}"'})
            # Grid counts must be positive ints; ranges must be 2-element numeric lists.
            for cnt in ('nx', 'ny'):
                v = ex.get(cnt)
                if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
                    errors.append({'field': f'explore.{cnt}', 'message': 'must be a positive integer'})
            for rng in ('xRange', 'yRange'):
                v = ex.get(rng)
                if v is not None and (not isinstance(v, list) or len(v) != 2
                                      or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)):
                    errors.append({'field': f'explore.{rng}', 'message': 'must be a [min, max] numeric pair'})

    # 5. EOS-table allowlist / path-traversal defense.
    allowed = known_eos_tables() if eosTables == 'discover' else eosTables
    _validate_eos_field(spec, 'sil', 'mantleEOS', allowed, errors)
    _validate_eos_field(spec, 'core', 'coreEOS', allowed, errors)

    return errors
