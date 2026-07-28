""" Fetch the trained SBI posterior artifacts for the amortized-inference service.

The ``.pt`` flow files (~6.9 MB total) are not committed to git — like the Perple_X EOS
tables, they are downloaded on demand. They live on the public upstream Hugging Face Space
``vsteven/planetprofile`` and are pulled into ``PlanetProfile/Inference/sbi_artifacts/``.

    python -m PlanetProfile.Inference.download_artifacts          # fetch any that are missing
    python -m PlanetProfile.Inference.download_artifacts --force  # re-download everything

Coverage note: the upstream Space ships validated flows for **Europa and Titan only**. There
are no trained posteriors for Callisto, Enceladus, or Ganymede (their configs exist, but a
flow would have to be trained). The API's inference registry serves whatever is present and
silently skips anything that hasn't been downloaded, so a partial fetch degrades gracefully.
"""
import os
import sys
import urllib.request
import urllib.error

_BASE = ('https://huggingface.co/spaces/vsteven/planetprofile/resolve/main/'
         'PlanetProfile/Inference/sbi_artifacts/')
_DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sbi_artifacts')

# The full set the Space ships (Europa + Titan). Deployed slots the API serves are a subset;
# the rest are retained for provenance / ablation reference.
ARTIFACTS = [
    'europa_galileo_v1p1_8D_posterior_1m.pt',
    'europa_seawater_v3_clipper_8D_posterior_1m.pt',
    'europa_clipper_v4_geodesy_11D_posterior_1m.pt',
    'europa_clipper_v4_geodesy_11D_posterior_1m_robustk2.pt',
    'europa_clipper_v5_geodesy_11D_posterior_1m.pt',
    'europa_clipper_v5_noinduction_7obs_posterior_1m.pt',
    'europa_clipper_v5_nok2_17obs_posterior_1m.pt',
    'europa_clipper_v6_freegrav_11D_posterior_1m.pt',
    'europa_clipper_v6_freegrav_noinduction_6obs_posterior_1m.pt',
    'europa_clipper_v6_freegrav_nok2_16obs_posterior_1m.pt',
    'europa_seawater_andrade_posterior_1m.pt',
    'europa_seawater_andrade_clipper_v2.pt',
    'titan_andrade_noocean_posterior.pt',
    'titan_freegrav_noocean_posterior_1m.pt',
    'titan_diff_noocean_andrade_test52_10D_v2.pt',
]


def _fetch(name, force=False):
    dest = os.path.join(_DEST, name)
    if os.path.isfile(dest) and not force and os.path.getsize(dest) > 0:
        return 'skip'
    url = _BASE + name
    tmp = dest + '.part'
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, 'wb') as f:
            f.write(r.read())
        # A .pt file is a zip archive; a stray HTML error page would start with '<'.
        with open(tmp, 'rb') as f:
            head = f.read(2)
        if head[:2] != b'PK':
            os.remove(tmp)
            return 'bad'
        os.replace(tmp, dest)
        return 'ok'
    except urllib.error.HTTPError as e:
        if os.path.isfile(tmp):
            os.remove(tmp)
        return f'http {e.code}'


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    force = '--force' in argv
    os.makedirs(_DEST, exist_ok=True)
    n_ok = n_skip = n_fail = 0
    for name in ARTIFACTS:
        status = _fetch(name, force=force)
        if status == 'ok':
            n_ok += 1; print(f'  downloaded  {name}')
        elif status == 'skip':
            n_skip += 1; print(f'  present     {name}')
        else:
            n_fail += 1; print(f'  FAILED ({status})  {name}')
    print(f'\n{n_ok} downloaded, {n_skip} already present, {n_fail} failed '
          f'-> {_DEST}')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
