""" The /schema payload: the single source of truth for the UI form.

``input_schema`` derives a JSON Schema from the same whitelist ``build_planet`` enforces,
by introspecting freshly constructed substructs (so it never drifts from the mapper).
``output_dictionary`` names every field in the single-run result with its unit and dtype.
``enums`` exposes the closed value sets. ``schema_payload`` bundles all three.
"""
import numpy as np

from PlanetProfile.Utilities.defineStructs import PlanetStruct
from PlanetProfile.API.mapper import SECTIONS, _is_settable, _TOPLEVEL
from PlanetProfile.API.validate import (
    KNOWN_BODIES, OCEAN_COMPS, EXPLORE_NAMES, EXPLORE_Z_NAMES, GRID_MODES, known_eos_tables,
)
from PlanetProfile.API.results import _LAYER_ARRAYS, _SUMMARY, _MASS, to_jsonable


def _json_type(value):
    """ Map a Python default value to a JSON Schema type (used to type each input field). """
    if isinstance(value, bool) or isinstance(value, np.bool_):
        return 'boolean'
    if isinstance(value, (int, np.integer)):
        return 'integer'
    if isinstance(value, (float, np.floating)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, (list, tuple, np.ndarray)):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    return 'number'  # None-defaulted inputs are overwhelmingly numeric


def input_schema():
    """ JSON Schema (draft-07 style) for a run request, generated from the whitelist. """
    fresh = PlanetStruct('Europa')
    properties = {
        'body': {'type': 'string', 'enum': sorted(KNOWN_BODIES)},
        'mode': {'type': 'string', 'enum': sorted({'single'} | GRID_MODES), 'default': 'single'},
        'run': {'type': 'object', 'properties': {
            k: {'type': 'boolean'} for k in (
                'calcNew', 'skipPlots', 'calcSeismic', 'calcConduct', 'calcViscosity',
                'calcOceanProps', 'calcInduction', 'calcGravity', 'noSaveFile',
                'saveMatlab', 'parallel')
        }},
    }
    for section, attrName in SECTIONS.items():
        ref = getattr(fresh, attrName)
        props = {}
        for key, current in vars(ref).items():
            if not _is_settable(key, current):
                continue
            prop = {'type': _json_type(current)}
            if current is not None and not isinstance(current, (list, tuple, np.ndarray, dict)):
                dflt = to_jsonable(current)   # NaN/Inf sentinels -> None (omit as a default)
                if dflt is not None:
                    prop['default'] = dflt
            if section == 'ocean' and key == 'comp':
                prop = {'type': 'string'}  # enum lives in enums(); CustomSolution* is open
            props[key] = prop
        properties[section] = {'type': 'object', 'properties': props}
    properties['planet'] = {'type': 'object', 'properties': {k: {'type': 'number'} for k in sorted(_TOPLEVEL)}}
    properties['explore'] = {'type': 'object', 'properties': {
        'xName': {'type': 'string', 'enum': sorted(EXPLORE_NAMES)},
        'yName': {'type': 'string', 'enum': sorted(EXPLORE_NAMES)},
        'zName': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(EXPLORE_Z_NAMES)}},
        'xRange': {'type': 'array', 'items': {'type': 'number'}},
        'yRange': {'type': 'array', 'items': {'type': 'number'}},
        'nx': {'type': 'integer'}, 'ny': {'type': 'integer'},
    }}
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'MoonMelodies run request',
        'type': 'object',
        'required': ['body'],
        'properties': properties,
    }


# Human-facing dictionary for the single-run result fields, keyed by result path.
_SUMMARY_UNITS = {
    'Mtot_kg': ('kg', 'Total body mass'),
    'CMR2mean': ('', 'Mean axial moment of inertia C/MR^2'),
    'zb_km': ('km', 'Ice shell thickness'),
    'D_km': ('km', 'Ocean thickness'),
    'Pb_MPa': ('MPa', 'Pressure at the ice-ocean interface'),
    'qSurf_Wm2': ('W/m^2', 'Surface heat flux'),
    'RsilMean_m': ('m', 'Mean silicate mantle outer radius'),
    'rhoSilMean_kgm3': ('kg/m^3', 'Mean silicate density'),
    'RcoreMean_m': ('m', 'Mean core outer radius'),
    'rhoOceanMean_kgm3': ('kg/m^3', 'Mean ocean density'),
    'sigmaOceanMean_Sm': ('S/m', 'Mean ocean electrical conductivity'),
    'Tmean_K': ('K', 'Mean ocean temperature'),
}
_LAYER_UNITS = {
    'P_MPa': 'MPa', 'T_K': 'K', 'r_m': 'm', 'phase': '', 'rho_kgm3': 'kg/m^3',
    'Cp_JkgK': 'J/kg/K', 'alpha_pK': '1/K', 'g_ms2': 'm/s^2', 'phi_frac': '',
    'sigma_Sm': 'S/m', 'kTherm_WmK': 'W/m/K', 'VP_kms': 'km/s', 'VS_kms': 'km/s',
    'QS': '', 'KS_GPa': 'GPa', 'GS_GPa': 'GPa', 'Ppore_MPa': 'MPa',
    'rhoMatrix_kgm3': 'kg/m^3', 'rhoPore_kgm3': 'kg/m^3', 'MLayer_kg': 'kg',
    'VLayer_m3': 'm^3', 'Htidal_Wm3': 'W/m^3', 'eta_Pas': 'Pa s',
}

PHASE_LEGEND = {
    0: 'liquid ocean', 1: 'ice I', 2: 'ice II', 3: 'ice III', 5: 'ice V', 6: 'ice VI',
    30: 'clathrate', 50: 'silicate', 100: 'liquid Fe', 105: 'solid Fe',
    110: 'liquid FeS', 115: 'solid FeS',
}


def output_dictionary():
    """ Name/unit/dtype/description for every field in the single-run result. """
    summary = {k: {'unit': u, 'dtype': 'number', 'description': d}
               for k, (u, d) in _SUMMARY_UNITS.items()}
    layers = {k: {'unit': _LAYER_UNITS.get(k, ''),
                  'dtype': 'integer[]' if k == 'phase' else 'number[]',
                  'description': f'Per-layer {k}'} for k in _LAYER_ARRAYS}
    mass = {k: {'unit': 'kg', 'dtype': 'number', 'description': f'Mass of {k}'} for k in _MASS}
    return {
        'summary': summary,
        'summary.mass': mass,
        'layers': layers,
        'gravity': {k: {'unit': '', 'dtype': 'complex{re,im}', 'description': f'Degree-2 {k} Love number'}
                    for k in ('h', 'l', 'k', 'delta')},
        'phaseLegend': {str(k): v for k, v in PHASE_LEGEND.items()},
    }


def enums():
    """ Closed value sets used by the UI form. """
    tables = known_eos_tables()
    return {
        'body': sorted(KNOWN_BODIES),
        'ocean.comp': sorted(OCEAN_COMPS) + ['CustomSolution*'],
        'mode': sorted({'single'} | GRID_MODES),
        'explore.xName': sorted(EXPLORE_NAMES),
        'explore.yName': sorted(EXPLORE_NAMES),
        'explore.zName': sorted(EXPLORE_Z_NAMES),
        'eosTables': sorted(tables) if tables else [],
    }


def schema_payload():
    """ The full GET /schema response: input schema, output dictionary, and enums. """
    return {
        'inputSchema': input_schema(),
        'outputDictionary': output_dictionary(),
        'enums': enums(),
    }
