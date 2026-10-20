"""Project CLI launcher: use the same .env precedence as the HTTP service.

Keep upstream load_environment() untouched (it deliberately uses override=False).
Inherited desktop credentials can otherwise override the project's provider key.
"""
import os
from pathlib import Path
import runpy
import sys
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'.env',override=True)
acceptance='--acceptance' in sys.argv
if acceptance:
    sys.argv.remove('--acceptance')
runpy.run_module('recommendation.terminal.acceptance' if acceptance else 'recommendation.terminal',run_name='__main__')
