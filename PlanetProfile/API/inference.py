""" Amortized Bayesian inference service for the API boundary.

Serves posterior samples from the downloaded, gate-validated SBI artifacts
(``PlanetProfile/Inference/sbi_artifacts/*.pt``) without touching the forward interior
engine. ``list_artifacts`` enumerates the deployable slots that are actually present on
disk; ``run_infer`` conditions a slot's amortized posterior on a set of observables and
returns posterior samples (for a corner plot) plus per-parameter summary statistics.

Design (mirrors mapper/results): a small hand-maintained ``_SLOTS`` registry maps a stable
slot id -> {artifact file, training config, label, guards, scope note}. Everything else is
read from the files themselves: param/obs metadata + bounds from the ``.pt`` artifact,
fiducial values + sigmas from the config JSON's ``observables`` map ({name: [value, sigma]}).
On load we assert the artifact's ``obs_names`` match the config observables, so a mis-mapped
slot is dropped rather than silently conditioning on the wrong measurement.

Guards (transcribed from ``sbi_artifacts/INDEX.md``, applied transparently and surfaced to
the client): ``x_obs_limits`` reject conditioning outside a flow's validated domain (e.g.
|Im k2| <= 0.15); ``truncate`` drop posterior draws outside a body's supported range (e.g.
Europa synodic support edge Tb >= 261.5 K) as a post-hoc mask.

torch is imported lazily on first use; the server process sets KMP_DUPLICATE_LIB_OK=TRUE so
it can host torch (it never imports the forward engine, so the libomp double-init hazard the
CLAUDE.md notes for training does not apply here).
"""
import os
import json
import logging

import numpy as np

from PlanetProfile import _ROOT

log = logging.getLogger('PlanetProfile.API.inference')

_ARTIFACT_DIR = os.path.join(_ROOT, 'Inference', 'sbi_artifacts')
_CONFIG_DIR = os.path.join(_ROOT, 'Inference', 'configs')

# Universal validated-domain cap for the imaginary-k2 channel (all deployed Europa flows
# share it, per INDEX). Applied only to slots whose obs include an Im_k2 channel.
_IM_K2_LIMIT = [0.0, 0.15]
# Europa ocean-model synodic support edge: draws below this Tb are outside training support.
_EUROPA_TB_FLOOR = 261.5


class InferError(ValueError):
    """ Raised on a guard violation / bad request; carries field-level messages so the
        route can return a 422 in the same shape as the other validators. """
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(f"{e['field']}: {e['message']}" for e in self.errors))


# slot id -> deployment metadata. Only slots whose artifact file exists on disk are served.
# Guards are conservative carryovers from INDEX.md; obs-match is asserted at load time.
_SLOTS = {
    'europa_galileo_v1p1': {
        'file': 'europa_galileo_v1p1_8D_posterior_1m.pt',
        'config': 'europa_galileo_v1p1_8D.json',
        'body': 'Europa',
        'label': 'Europa · Galileo v1.1 (8D — all gates pass)',
        'im_k2_cap': True, 'tb_floor': _EUROPA_TB_FLOOR,
        'scope': 'Honest Galileo-era framing: only CMR2 (GC21) and the synodic support cut '
                 'are data; k2/h2 are labeled hypothetical channels. All validation gates pass.',
    },
    'europa_seawater_v3': {
        'file': 'europa_seawater_v3_clipper_8D_posterior_1m.pt',
        'config': 'europa_seawater_andrade_clipper_v3_8D.json',
        'body': 'Europa',
        'label': 'Europa · Seawater v3 Clipper (8D, induction channels)',
        'im_k2_cap': True, 'tb_floor': _EUROPA_TB_FLOOR,
        'scope': 'Seawater ocean with Clipper induction channels (signed Bind_* families) '
                 'plus k2/h2. Conditions on |Im k2| (abs-folded) within the validated domain.',
    },
    'europa_clipper_v4_geodesy': {
        'file': 'europa_clipper_v4_geodesy_11D_posterior_1m.pt',
        'config': 'europa_clipper_v4_geodesy_11D.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v4 geodesy (11D, non-hydrostatic u)',
        'im_k2_cap': True, 'tb_floor': None,   # 2-D Tb×w support: no Tb truncation
        'scope': 'Projected Clipper geodesy (dC20/dC22 non-hydrostatic). Interior scalars are '
                 'prior-dominated; the reportable constraint is the non-hydrostatic u upper limit.',
    },
    'europa_clipper_v6_freegrav': {
        'file': 'europa_clipper_v6_freegrav_11D_posterior_1m.pt',
        'config': 'europa_clipper_v6_freegrav_11D.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v6 free-gravity (11D)',
        'im_k2_cap': True, 'tb_floor': None,
        'scope': 'CMR2 dropped as an observable (avoids double-counting C22); interior '
                 'constrained by k2/h2 + induction. C/MR² shown as display reference only.',
    },
    'titan_noocean': {
        'file': 'titan_andrade_noocean_posterior.pt',
        'config': 'test50_titan_noocean_andrade_8D.json',
        'body': 'Titan',
        'label': 'Titan · Andrade no-ocean (8D — all gates green)',
        'im_k2_cap': False, 'tb_floor': None,
        'scope': 'Differentiated no-ocean Titan (Andrade rheology). All validation gates green '
                 'within domain.',
    },
    'titan_freegrav': {
        'file': 'titan_freegrav_noocean_posterior_1m.pt',
        'config': 'titan_freegrav_noocean.json',
        'body': 'Titan',
        'label': 'Titan · free-gravity no-ocean (geodesy + tidal k2)',
        'im_k2_cap': False, 'tb_floor': None,
        'scope': 'No-ocean Titan conditioned on free (dC20/dC22) gravity plus the measured '
                 'degree-2 tidal Love number (Re/Im k2). Titan-specific k2 domain, not Europa\'s.',
    },
    # Clipper v5 (v4 geodesy + ice-thickness reparam) deployed trio: full channels + two ablations.
    'europa_clipper_v5_geodesy': {
        'file': 'europa_clipper_v5_geodesy_11D_posterior_1m.pt',
        'config': 'europa_clipper_v5_geodesy_11D.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v5 geodesy (11D, ice-thickness reparam)',
        'im_k2_cap': True, 'tb_floor': None,
        'scope': 'v4 geodesy re-parameterized on ice-shell thickness; induction carries the '
                 'ice-thickness↔salinity correlation. Guards inherited from v4.',
    },
    'europa_clipper_v5_noinduction': {
        'file': 'europa_clipper_v5_noinduction_7obs_posterior_1m.pt',
        'config': 'europa_clipper_v5_noinduction_7obs.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v5 — no induction (7 obs, ablation)',
        'im_k2_cap': True, 'tb_floor': None,
        'scope': 'v5 with induction channels removed — isolates what magnetic induction '
                 'contributes to the interior constraint (weaker D↔salinity correlation).',
    },
    'europa_clipper_v5_nok2': {
        'file': 'europa_clipper_v5_nok2_17obs_posterior_1m.pt',
        'config': 'europa_clipper_v5_nok2_17obs.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v5 — no k2/h2 (17 obs, ablation)',
        'im_k2_cap': True, 'tb_floor': None,       # cap auto-skips (no Im_k2 channel)
        'scope': 'v5 with the tidal Love-number channels removed — the interior constrained by '
                 'gravity + induction alone.',
    },
    'europa_clipper_v6_noinduction': {
        'file': 'europa_clipper_v6_freegrav_noinduction_6obs_posterior_1m.pt',
        'config': 'europa_clipper_v6_freegrav_noinduction_6obs.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v6 free-gravity — no induction (6 obs, ablation)',
        'im_k2_cap': True, 'tb_floor': None,
        'scope': 'v6 free-gravity with induction removed. CMR2 dropped as an observable; '
                 'interior from k2/h2 alone.',
    },
    'europa_clipper_v6_nok2': {
        'file': 'europa_clipper_v6_freegrav_nok2_16obs_posterior_1m.pt',
        'config': 'europa_clipper_v6_freegrav_nok2_16obs.json',
        'body': 'Europa',
        'label': 'Europa · Clipper v6 free-gravity — no k2/h2 (16 obs, ablation)',
        'im_k2_cap': True, 'tb_floor': None,       # cap auto-skips (no Im_k2 channel)
        'scope': 'v6 free-gravity with tidal channels removed — interior from gravity + '
                 'induction alone.',
    },
}

# Module cache of loaded SBIRunner objects (torch deserialize is paid once per slot).
_RUNNERS = {}
_SUMMARIES = None   # cached list_artifacts() payload


def _load_config_observables(configFile):
    """ Return ({name: value}, {name: sigma}) from a training config's observables map. """
    path = os.path.join(_CONFIG_DIR, configFile)
    with open(path) as f:
        cfg = json.load(f)
    obs = cfg.get('observables') or {}
    fiducial = {k: float(v[0]) for k, v in obs.items()}
    sigma = {k: float(v[1]) for k, v in obs.items()}
    return fiducial, sigma


def _runner_for(slotId):
    """ Load (and cache) the SBIRunner for a slot; None if unavailable/mis-mapped. """
    if slotId in _RUNNERS:
        return _RUNNERS[slotId]
    slot = _SLOTS.get(slotId)
    if slot is None:
        return None
    artifactPath = os.path.join(_ARTIFACT_DIR, slot['file'])
    if not os.path.isfile(artifactPath):
        return None
    from PlanetProfile.Inference.sbi_runner import SBIRunner
    runner = SBIRunner.load_artifact(artifactPath)
    # Safety: the artifact's obs must match the config we read fiducials from, else we'd
    # condition on the wrong measurement. Drop the slot (don't serve) on mismatch.
    fiducial, _sigma = _load_config_observables(slot['config'])
    if set(runner.obs_names) != set(fiducial.keys()):
        log.warning(f"inference slot '{slotId}': obs_names {runner.obs_names} != config "
                    f"observables {list(fiducial)}; slot disabled.")
        _RUNNERS[slotId] = None
        return None
    _RUNNERS[slotId] = runner
    return runner


def _guards_for(slotId, runner):
    """ Build the guard descriptor (validated conditioning limits + sample truncations). """
    slot = _SLOTS[slotId]
    xObsLimits = {}
    if slot.get('im_k2_cap') and 'Im_k2' in runner.obs_names:
        xObsLimits['Im_k2'] = list(_IM_K2_LIMIT)
    truncate = {}
    if slot.get('tb_floor') is not None and 'Tb_K' in runner.param_names:
        truncate['Tb_K'] = [slot['tb_floor'], None]
    return xObsLimits, truncate


def _summary_for(slotId):
    """ Build the selector/form summary for one slot (or None if unavailable). """
    runner = _runner_for(slotId)
    if runner is None:
        return None
    slot = _SLOTS[slotId]
    fiducial, sigma = _load_config_observables(slot['config'])
    xObsLimits, truncate = _guards_for(slotId, runner)
    bounds = runner.artifact_meta.get('param_bounds')
    return {
        'id': slotId,
        'body': slot['body'],
        'label': slot['label'],
        'paramNames': list(runner.param_names),
        'paramLabels': list(runner.param_labels),
        'paramUnits': list(runner.param_units),
        'paramBounds': _jsonable(bounds),
        'obsNames': list(runner.obs_names),
        'channelConventions': dict(runner.channel_conventions or {}),
        'imagConvention': runner.imag_convention,
        'fiducial': {k: _jsonable(v) for k, v in fiducial.items()},
        'sigma': {k: _jsonable(v) for k, v in sigma.items()},
        'guards': {'xObsLimits': xObsLimits, 'truncate': truncate},
        'scopeNote': slot['scope'],
    }


def list_artifacts():
    """ Enumerate the deployable inference slots present on disk (cached). """
    global _SUMMARIES
    if _SUMMARIES is None:
        out = []
        for slotId in _SLOTS:
            try:
                s = _summary_for(slotId)
            except Exception as e:            # noqa: BLE001 -- a bad slot must not break the list
                log.warning(f"inference slot '{slotId}' failed to load: {e}")
                s = None
            if s is not None:
                out.append(s)
        _SUMMARIES = out
    return _SUMMARIES


def get_artifact(slotId):
    """ Full metadata for one slot, or None if not available. """
    for s in list_artifacts():
        if s['id'] == slotId:
            return s
    return None


def run_infer(slotId, x_obs, n_samples=10000, seed=None):
    """ Condition the slot's posterior on x_obs and return samples + summary.

        Raises InferError (-> 422) on unknown slot, unknown/invalid observable, or a
        conditioning value outside the flow's validated domain. Applies the slot's sample
        truncation as a post-hoc mask and reports the dropped fraction.
    """
    summary = get_artifact(slotId)
    if summary is None:
        raise InferError([{'field': 'artifact', 'message': f'unknown or unavailable slot "{slotId}"'}])
    runner = _runner_for(slotId)

    errors = []
    obsSet = set(runner.obs_names) | {'abs_Im_k2'}
    xClean = {}
    for k, v in (x_obs or {}).items():
        if k not in obsSet:
            errors.append({'field': f'x_obs.{k}', 'message': f'not an observable of "{slotId}"'})
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            errors.append({'field': f'x_obs.{k}', 'message': 'must be a finite number'})
            continue
        if not np.isfinite(fv):
            errors.append({'field': f'x_obs.{k}', 'message': 'must be a finite number'})
            continue
        xClean[k] = fv
    # Fill any missing observable from the fiducial so a partial form still conditions fully.
    for name in runner.obs_names:
        if name not in xClean and not (name == 'Im_k2' and 'abs_Im_k2' in xClean):
            xClean[name] = summary['fiducial'].get(name)
            if xClean[name] is None:
                errors.append({'field': f'x_obs.{name}', 'message': 'required (no fiducial default)'})

    # Enforce validated conditioning limits.
    xObsLimits = summary['guards']['xObsLimits']
    for name, (lo, hi) in xObsLimits.items():
        val = xClean.get(name, xClean.get('abs_Im_k2') if name == 'Im_k2' else None)
        if val is not None:
            v = abs(val) if (name == 'Im_k2' and runner.channel_conventions.get('Im_k2', runner.imag_convention) == 'abs') else val
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                errors.append({'field': f'x_obs.{name}',
                               'message': f'{name}={val:g} outside validated domain '
                                          f'[{lo}, {hi}]; use MCMC beyond it'})
    if errors:
        raise InferError(errors)

    samples = runner.sample_posterior(xClean, n_samples=int(n_samples), seed=seed)
    samples = np.asarray(samples, dtype=float)   # (N, D) in param_names order

    # Post-hoc truncation (e.g. Tb >= 261.5): mask rows outside the supported range.
    truncate = summary['guards']['truncate']
    applied = []
    nRaw = samples.shape[0]
    for pname, (lo, hi) in truncate.items():
        if pname in runner.param_names:
            col = runner.param_names.index(pname)
            keep = np.ones(samples.shape[0], dtype=bool)
            if lo is not None:
                keep &= samples[:, col] >= lo
            if hi is not None:
                keep &= samples[:, col] <= hi
            dropped = int((~keep).sum())
            samples = samples[keep]
            applied.append({'type': 'truncate', 'param': pname, 'range': [lo, hi],
                            'droppedFraction': (dropped / nRaw) if nRaw else 0.0})

    stats = _summarize(samples, runner.param_names)

    return {
        'artifact': slotId,
        'body': summary['body'],
        'paramNames': list(runner.param_names),
        'paramLabels': list(runner.param_labels),
        'paramUnits': list(runner.param_units),
        'paramBounds': summary['paramBounds'],
        'nRequested': int(n_samples),
        'nReturned': int(samples.shape[0]),
        'samples': _jsonable(samples),
        'summary': stats,
        'conditioning': {
            'xObs': {k: _jsonable(v) for k, v in xClean.items()},
            'obsNames': list(runner.obs_names),
            'imagConvention': runner.imag_convention,
        },
        'guards': {'applied': applied, 'xObsLimits': xObsLimits},
        'scopeNote': summary['scopeNote'],
        'meta': {
            'seed': seed,
            'gitSha': runner.artifact_meta.get('git_sha'),
            'nTrain': runner.artifact_meta.get('n_train_effective'),
            'sbiVersion': runner.artifact_meta.get('sbi_version'),
            'createdUtc': runner.artifact_meta.get('created_utc'),
        },
    }


def _summarize(samples, paramNames):
    """ Per-parameter {mean, std, median, p5, p16, p84, p95}. """
    out = {}
    if samples.shape[0] == 0:
        return {p: None for p in paramNames}
    q = np.percentile(samples, [5, 16, 50, 84, 95], axis=0)
    mean = samples.mean(axis=0)
    std = samples.std(axis=0)
    for i, p in enumerate(paramNames):
        out[p] = {
            'mean': _jsonable(mean[i]), 'std': _jsonable(std[i]),
            'median': _jsonable(q[2, i]),
            'p5': _jsonable(q[0, i]), 'p16': _jsonable(q[1, i]),
            'p84': _jsonable(q[3, i]), 'p95': _jsonable(q[4, i]),
        }
    return out


def _jsonable(obj):
    """ numpy -> plain JSON; NaN/Inf -> None. """
    if obj is None:
        return None
    if isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return [_jsonable(x) for x in obj.tolist()]
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return obj
