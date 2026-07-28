# Training new-moon SBI posteriors

The upstream Hugging Face Space ships trained flows for **Europa and Titan only**. The
posteriors for **Enceladus, Ganymede, and Callisto** in this fork were trained locally with
the in-repo pipeline (they have configs but no upstream flow). This is the exact recipe, so
anyone can reproduce or extend them.

## Pipeline

Each moon goes: **config → structure cache → training set → NSF flow → SBC gate**. The
structure cache pre-computes the interior/Love-number forward model on a Tb grid, so
per-draw generation is a fast interpolation (~4.5 ms/draw, ~0.004 s/requested draw with the
mass-conservation support cut) rather than a full PlanetProfile run.

Run everything in the scientific env with `KMP_DUPLICATE_LIB_OK=TRUE` (torch/libomp) and keep
PlanetProfile-data generation and torch training in **separate processes**.

```bash
BODY=ganymede
CFG=PlanetProfile/Inference/configs/ganymede_pureh2o_andrade_8D.json
ART=PlanetProfile/Inference/sbi_artifacts/ganymede_pureh2o_andrade_8D_posterior.pt

# 1. Structure cache (the only PlanetProfile/EOS/gravity-heavy step). Ganymede's config
#    template is null, so pass --template; Enceladus/Callisto have it set.
python -m PlanetProfile.Inference.build_phase_c1_cache --config $CFG --n-grid 13 \
    --template PlanetProfile.Default.Ganymede.PPGanymede --force

# 2. Training set -> .npz  (shard across cores with distinct seed/noise_seed, concat rows).
#    Request ~1M for the 6-8D moons; the k2 moons reject ~15-65% at the rho_sil mass gate,
#    so request 2M for Callisto to keep ~700k.
python -c "import json,numpy as np; from PlanetProfile.Inference.inference_core import InferenceConfig; \
from PlanetProfile.Inference.sbi_runner import SBIRunner; \
c=json.load(open('$CFG')); c['mode']='sbi'; r=SBIRunner(InferenceConfig.from_dict(c)); \
th,x,st=r.generate_training_set(1000000, seed=100, noise_seed=5000); \
np.savez('train.npz', theta=th, x=x, param_names=r.param_names, obs_names=r.obs_names); print(st)"

# 3. Train the NSF normalizing flow + save the schema-v1 artifact (torch-only process).
python -m PlanetProfile.Inference.train_sbi_artifact --dataset train.npz --config $CFG \
    --density-estimator nsf --seed 42 --output $ART

# 4. SBC calibration gate (the ratified primary gate; NO reference MCMC needed).
#    Use a held-out .npz to avoid the cache-cwd resolution issue in --config generate mode.
python -m PlanetProfile.Inference.validate_sbi sbc --artifact $ART --dataset heldout.npz \
    --n-sbc 300 --num-posterior-samples 500 --seed 42 --output-dir val/$BODY
```

Then add a slot to `_SLOTS` in `PlanetProfile/API/inference.py` (file + config + body + label +
`im_k2_cap` (True iff an `Im_k2` channel exists) + scope) and restart the API.

## Results (this fork)

| Moon | Config | Params / obs | Kept sims | SBC | Note |
|---|---|---|---|---|---|
| Enceladus | `enceladus_cassini_smoke_6D` | 6D / C20,C22,libration | 1.0M | **PASS** (min KS p 0.13) | all 6 calibrated |
| Ganymede | `ganymede_pureh2o_andrade_8D` | 8D / CMR2,k2,h2 | 837k | **PASS** (min KS p 0.07) | all 8 calibrated |
| Callisto | `callisto_nacl_andrade_8D` | 8D / CMR2,k2,h2,induction (11 obs) | 725k | 7/8 | `Tb` prior-dominated (KS p ~0.01); intrinsic — matches the deployed Europa-Clipper flows, which also SBC-fail on diffuse `Tb` |

`Tb` (ice-base temperature) is weakly identified by CMR2/k2/h2/induction; more sims did not
help (0.0155 at 362k → 0.0092 at 725k), and a MAF flow on the same 725k dataset was *worse*
(SBC-failed 3 params vs NSF's 1), so `Tb` fails in both flow families — it is treated as
diffuse rather than a defect, per the upstream precedent. NSF is the deployed Callisto flow. The primary bar is **SBC-calibrated + samples-in-prior**; the
`limits` and `crosscheck` gates are secondary evidence (crosscheck needs a reference MCMC).
