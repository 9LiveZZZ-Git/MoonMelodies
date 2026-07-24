# data-assets/ — Large-table manifests (not the binaries)

This directory holds **manifests and fetch/checksum metadata** for the large
Perple_X / EOS tables — not the tables themselves. The live tables are
downloaded on install (`PlanetProfile/install.py`) into
`PlanetProfile/Thermodynamics/EOStables/Perple_X/` and are excluded from the
wheel (`MANIFEST.in`).

Planned contents:
- `perplex_manifest.txt` — file list + SHA-256 checksums so install can verify
  downloaded tables. Generate with:
  `(cd PlanetProfile/Thermodynamics/EOStables/Perple_X && shasum -a 256 *.tab *.mat) > data-assets/perplex_manifest.txt`

This supports the deferred git-history purge (Phase 4): once the checksummed
manifest exists, the tracked `*.tab`/`*.mat` blobs can be removed from history
while install restores them on disk.
