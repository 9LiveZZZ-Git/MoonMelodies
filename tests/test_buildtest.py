"""Repository-level smoke test that runs the full PlanetProfile build test suite.

This shells out to ``python -m PlanetProfile.BuildTest`` so CI can gate changes
without relocating the in-package ``PlanetProfile/Test/`` harness (which
``BuildTest.py`` imports from a fixed location).

Requirements and caveats:
- Needs the full scientific stack installed (numpy, scipy, SeaFreeze, gsw,
  spiceypy, MoonMag, reaktoro, pyalma3, matplotlib) and the Perple_X tables
  downloaded via ``python -m PlanetProfile.install``.
- It is SLOW (minutes). Treat it as a CI gate, not a fast unit test.
"""
import subprocess
import sys


def test_buildtest_full_suite():
    """The full BuildTest suite must complete with exit code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "PlanetProfile.BuildTest"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "PlanetProfile.BuildTest failed "
        f"(exit {result.returncode}).\n--- stdout tail ---\n"
        f"{result.stdout[-4000:]}\n--- stderr tail ---\n{result.stderr[-4000:]}"
    )


if __name__ == "__main__":
    test_buildtest_full_suite()
    print("BuildTest smoke test passed.")
