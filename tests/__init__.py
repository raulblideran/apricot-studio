"""Clipper's test suite.

Uses only the standard library's unittest plus PyQt6.QtTest, both of which are
already present, so the project keeps its zero-dependency property and the suite
runs unchanged inside the flatpak.

    ./run-tests.sh              everything
    ./run-tests.sh --fast       skip the tests that invoke ffmpeg
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
