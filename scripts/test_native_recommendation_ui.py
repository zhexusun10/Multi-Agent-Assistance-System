"""Compatibility entry point for the current real-browser recommendation acceptance.

The normal UI now collects real viewing intervals; the old manual-sample flow is
available only in developer mode. Keep this historical command usable.
"""
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
runpy.run_path(str(HERE / "test_browser_interest_ui.py"), run_name="__main__")
runpy.run_path(str(HERE / "test_browser_interest_boundaries.py"), run_name="__main__")
