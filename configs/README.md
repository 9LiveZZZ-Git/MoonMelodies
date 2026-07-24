# configs/ — Sample UserConfigs templates (optional)

Reserved for sample `UserConfigs/` templates used to seed headless/server runs
without the interactive first-import copy step. The engine's canonical config
defaults live in `PlanetProfile/defaultConfig*.py`; on first import they are
copied into a working directory's `UserConfigs/` (now non-interactively when
stdin is not a TTY — see Phase 0).
