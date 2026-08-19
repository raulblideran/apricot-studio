"""Apricot Studio's test suite.

Uses only the standard library's unittest plus PyQt6.QtTest, both of which are
already present, so the project keeps its zero-dependency property and the suite
runs unchanged inside the flatpak.

    ./run-tests.sh              everything
    ./run-tests.sh --fast       skip the tests that invoke ffmpeg
"""

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Keep the suite out of the real config, for the same reason the fixtures are
# synthesised rather than read from a video library: every window built here
# would otherwise leave its accent, its recent files and its geometry in the
# settings the user's own copy reads on the next launch, and a test that resizes
# a window would be deciding how their app opens.
#
# This has to be the environment rather than QSettings.setPath, which Qt ignores
# once it has resolved where the native settings live -- and it has to happen
# before anything imports Qt, which is why it sits in the package itself.
_CONFIG = os.path.join(tempfile.gettempdir(), f"apricot-test-config-{os.getuid()}")
os.makedirs(_CONFIG, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = _CONFIG
