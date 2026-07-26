""" Serialize a completed run to result.json (+ an artifact manifest).

``extract_single`` reads the well-known Planet attributes that ``WriteProfile`` writes
(the 23 per-layer columns plus the header scalars) into the single-run result shape.
``to_jsonable`` / ``PPJSONEncoder`` make numpy arrays, numpy scalars, complex numbers,
and non-finite floats safe to emit as strict JSON (complex -> {re, im}; NaN/Inf -> null).
``build_manifest`` enumerates the on-disk artifacts the engine dropped in the jobdir.
"""
import os
import json
import numpy as np

# 23 per-layer profile columns -> (attrPath). A tuple means a nested substruct attr.
_LAYER_ARRAYS = {
    'P_MPa': ('P_MPa',),
    'T_K': ('T_K',),
    'r_m': ('r_m',),
    'phase': ('phase',),
    'rho_kgm3': ('rho_kgm3',),
    'Cp_JkgK': ('Cp_JkgK',),
    'alpha_pK': ('alpha_pK',),
    'g_ms2': ('g_ms2',),
    'phi_frac': ('phi_frac',),
    'sigma_Sm': ('sigma_Sm',),
    'kTherm_WmK': ('kTherm_WmK',),
    'VP_kms': ('Seismic', 'VP_kms'),
    'VS_kms': ('Seismic', 'VS_kms'),
    'QS': ('Seismic', 'QS'),
    'KS_GPa': ('Seismic', 'KS_GPa'),
    'GS_GPa': ('Seismic', 'GS_GPa'),
    'Ppore_MPa': ('Ppore_MPa',),
    'rhoMatrix_kgm3': ('rhoMatrix_kgm3',),
    'rhoPore_kgm3': ('rhoPore_kgm3',),
    'MLayer_kg': ('MLayer_kg',),
    'VLayer_m3': ('VLayer_m3',),
    'Htidal_Wm3': ('Htidal_Wm3',),
    'eta_Pas': ('eta_Pas',),
}

# Scalar summary fields -> attrPath.
_SUMMARY = {
    'Mtot_kg': ('Mtot_kg',),
    'CMR2mean': ('CMR2mean',),
    'zb_km': ('zb_km',),
    'D_km': ('D_km',),
    'Pb_MPa': ('Pb_MPa',),
    'qSurf_Wm2': ('qSurf_Wm2',),
    'RsilMean_m': ('Sil', 'Rmean_m'),
    'rhoSilMean_kgm3': ('Sil', 'rhoMean_kgm3'),
    'RcoreMean_m': ('Core', 'Rmean_m'),
    'rhoOceanMean_kgm3': ('Ocean', 'rhoMean_kgm3'),
    'sigmaOceanMean_Sm': ('Ocean', 'sigmaMean_Sm'),
    'Tmean_K': ('Ocean', 'Tmean_K'),
}

_MASS = {
    'MH2O_kg': ('MH2O_kg',),
    'Mrock_kg': ('Mrock_kg',),
    'Mcore_kg': ('Mcore_kg',),
    'Mocean_kg': ('Mocean_kg',),
    'Mice_kg': ('Mice_kg',),
    'Msalt_kg': ('Msalt_kg',),
}

_GRAVITY_COMPLEX = ('h', 'l', 'k', 'delta')
_GRAVITY_SCALAR = ('libration_m', 'Torb_s', 'eccentricity',
                   'hAmp', 'lAmp', 'kAmp', 'deltaAmp',
                   'hPhase', 'lPhase', 'kPhase', 'deltaPhase')

_MIME = {
    '.txt': 'text/plain', '.json': 'application/json', '.pkl': 'application/octet-stream',
    '.mat': 'application/octet-stream', '.png': 'image/png', '.pdf': 'application/pdf',
    '.eps': 'application/postscript', '.bm': 'text/plain', '.csv': 'text/csv',
}
_KIND = {
    '.txt': 'profile', '.json': 'result', '.pkl': 'pickle', '.mat': 'matlab',
    '.png': 'figure', '.pdf': 'figure', '.eps': 'figure', '.bm': 'seismic', '.csv': 'table',
}


def to_jsonable(obj):
    """ Recursively convert numpy / complex / non-finite values into strict-JSON types.

        ndarray -> nested list, numpy scalar -> Python scalar, complex -> {re, im},
        NaN/Inf -> None (JSON has no NaN; JS JSON.parse rejects the literal).
    """
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (complex, np.complexfloating)):
        re, im = float(obj.real), float(obj.imag)
        return {'re': re if np.isfinite(re) else None, 'im': im if np.isfinite(im) else None}
    if isinstance(obj, np.ndarray):
        return [to_jsonable(x) for x in obj.tolist()] if obj.dtype == object else to_jsonable(obj.tolist())
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    # Fallback: last-resort string form so serialization never hard-fails.
    return str(obj)


class PPJSONEncoder(json.JSONEncoder):
    """ json.JSONEncoder that routes unknown/numpy/complex objects through to_jsonable. """
    def default(self, o):
        return to_jsonable(o)


def _get(obj, path, default=np.nan):
    cur = obj
    for name in path:
        cur = getattr(cur, name, None)
        if cur is None:
            return default
    return cur


def _array(Planet, path, n):
    arr = _get(Planet, path, None)
    if arr is None:
        return [None] * n
    return to_jsonable(np.asarray(arr))


def extract_single(Planet, manifest=None, figures=None):
    """ Build the single-run result dict (spec section 3.2) from a completed Planet. """
    valid = bool(getattr(Planet.Do, 'VALID', False))
    nTotal = int(getattr(Planet.Steps, 'nTotal', 0) or 0)

    summary = {k: to_jsonable(_get(Planet, p)) for k, p in _SUMMARY.items()}
    summary['mass'] = {k: to_jsonable(_get(Planet, p)) for k, p in _MASS.items()}

    gravity = {k: to_jsonable(_get(Planet.Gravity, (k,))) for k in _GRAVITY_COMPLEX}
    gravity.update({k: to_jsonable(_get(Planet.Gravity, (k,))) for k in _GRAVITY_SCALAR})

    Mag = Planet.Magnetic
    induction = {
        'calcedExc': to_jsonable(getattr(Mag, 'calcedExc', None)),
        'Texc_hr': to_jsonable(getattr(Mag, 'Texc_hr', None)),
        'Amp': to_jsonable(getattr(Mag, 'Amp', None)),
        'phase': to_jsonable(getattr(Mag, 'phase', None)),
        'Bi1xyz_nT': {c: to_jsonable((getattr(Mag, 'Bi1xyz_nT', None) or {}).get(c))
                      for c in ('x', 'y', 'z')},
    }

    layers = {name: _array(Planet, path, nTotal) for name, path in _LAYER_ARRAYS.items()}

    trade = {
        'RsilTrade_m': _array(Planet, ('Sil', 'Rtrade_m'), 0),
        'RcoreTrade_m': _array(Planet, ('Core', 'Rtrade_m'), 0),
        'rhoSilTrade_kgm3': _array(Planet, ('Sil', 'rhoTrade_kgm3'), 0),
    }

    ocean = {
        'aqueousSpecies': to_jsonable(getattr(Planet.Ocean, 'aqueousSpecies', None)),
        'aqueousSpeciesAmount_mol': to_jsonable(getattr(Planet.Ocean, 'aqueousSpeciesAmount_mol', None)),
        'pHSeafloor': to_jsonable(getattr(Planet.Ocean, 'pHSeafloor', None)),
        'pHTop': to_jsonable(getattr(Planet.Ocean, 'pHTop', None)),
    }

    return {
        'meta': {
            'body': Planet.name,
            'bodyname': Planet.bodyname,
            'valid': valid,
            'invalidReason': str(getattr(Planet, 'invalidReason', '') or ''),
            'nTotal': nTotal,
            'artifacts': manifest if manifest is not None else [],
            'figures': figures or {},
        },
        'summary': summary,
        'gravity': gravity,
        'induction': induction,
        'layers': layers,
        'trade': trade,
        'oceanProps': ocean,
    }


# Curated 2-D scalar fields the grid may expose for coloring the heatmap. Only those
# actually present (non-None) on the extracted base struct are emitted; the requested
# zName(s) are always included first so the client's default coloring is available.
_GRID_FIELDS = [
    'D_km', 'zb_km', 'CMR2mean', 'Mtot_kg', 'Tmean_K', 'sigmaMean_Sm',
    'rhoOceanMean_kgm3', 'rhoSilMean_kgm3', 'rhoCoreMean_kgm3', 'Rcore_km',
    'zSeafloor_km', 'eLid_km', 'Pseafloor_MPa', 'phiSeafloor_frac',
    'kLoveAmp', 'hLoveAmp', 'lLoveAmp',
]


def extract_grid(Exploration, Params, manifest=None):
    """ Build the ExploreOgram grid result dict (spec section 3.3) from a completed
        ExplorationResultsStruct. Every z field is a 2-D (nx x ny) array; invalid cells
        are null (NaN->null), complex Love numbers become {re, im}. """
    base = Exploration.base

    def grid2d(name):
        arr = getattr(base, name, None)
        return None if arr is None else to_jsonable(np.asarray(arr))

    zName = getattr(Params.Explore, 'zName', None)
    zNames = list(zName) if isinstance(zName, (list, tuple)) else ([zName] if zName else [])

    wanted = []
    for nm in [z for z in zNames if z] + _GRID_FIELDS:
        if nm not in wanted and getattr(base, nm, None) is not None:
            wanted.append(nm)

    grid = {nm: grid2d(nm) for nm in wanted}
    grid['VALID'] = grid2d('VALID')
    grid['invalidReason'] = grid2d('invalidReason')

    validGrid = getattr(base, 'VALID', None)
    valid = bool(np.any(np.asarray(validGrid))) if validGrid is not None else False
    logScale = getattr(Params.Explore, 'exploreLogScale', []) or []

    return {
        'meta': {
            'body': Exploration.bodyname,
            'bodyname': Exploration.bodyname,
            'mode': 'exploreogram',
            'valid': valid,
            'nx': int(Exploration.nx), 'ny': int(Exploration.ny),
            'xName': Exploration.xName, 'yName': Exploration.yName,
            'zNames': [z for z in zNames if z],
            'availableZ': wanted,
            'artifacts': manifest if manifest is not None else [],
        },
        'grid': grid,
        'axes': {
            'xData': grid2d_from(Exploration.xData),
            'yData': grid2d_from(Exploration.yData),
            'xScale': 'log' if Exploration.xName in logScale else 'linear',
            'yScale': 'log' if Exploration.yName in logScale else 'linear',
            'xUnits': getattr(Exploration, 'xUnits', None),
            'yUnits': getattr(Exploration, 'yUnits', None),
        },
        'moi': {
            'CMR2str': getattr(Exploration, 'CMR2str', None),
            'Cmeasured': to_jsonable(getattr(Exploration, 'Cmeasured', None)),
            'Cupper': to_jsonable(getattr(Exploration, 'Cupper', None)),
            'Clower': to_jsonable(getattr(Exploration, 'Clower', None)),
        },
    }


def grid2d_from(arr):
    """ to_jsonable an axis mesh (2-D ndarray) or None. """
    return None if arr is None else to_jsonable(np.asarray(arr))


def build_manifest(jobdir, hrefBase=None):
    """ Enumerate on-disk artifacts under jobdir as [{name, kind, mime, bytes, href}].

        ``name`` is the jobdir-relative path (the artifact id); ``href`` is filled only
        when hrefBase is given (the Rust layer supplies the run-scoped URL otherwise).
    """
    manifest = []
    jobdir = os.path.abspath(jobdir)
    for root, _dirs, files in os.walk(jobdir):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, jobdir).replace(os.sep, '/')
            ext = os.path.splitext(fn)[1].lower()
            try:
                nbytes = os.path.getsize(full)
            except OSError:
                nbytes = None
            entry = {
                'name': rel,
                'kind': _KIND.get(ext, 'other'),
                'mime': _MIME.get(ext, 'application/octet-stream'),
                'bytes': nbytes,
            }
            if hrefBase is not None:
                entry['href'] = f'{hrefBase.rstrip("/")}/{rel}'
            manifest.append(entry)
    return manifest


def write_result_json(result, jobdir, name='result.json'):
    """ Write result (any jsonable dict) to jobdir/name as strict JSON; return its path. """
    path = os.path.join(jobdir, name)
    with open(path, 'w') as f:
        json.dump(result, f, cls=PPJSONEncoder, allow_nan=False)
    return path
