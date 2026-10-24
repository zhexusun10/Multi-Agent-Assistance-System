#!/usr/bin/env bash
# P7 final INVENTORY grep check: the re-run commands of INVENTORY.md, over tracked AND untracked (not ignored)
# files. Every remaining hit must be listed as resolved / keep / retired in INVENTORY.md section F.
# Usage (repository root): bash evaluation/recommendation-exploration-2026-10-23/p7/inventory_check.sh
cd "$(dirname "$0")/../../.." || exit 1
g() { git grep --untracked --exclude-standard -nE "$@" | cut -c1-200; }
echo "== 1 python 4-limits";        g "le=4\b|max_length=4|\[:4\]|\[: ?4\]|len\(pool\) > 4|self.pool\) > 4" -- 'recommendation/*.py' 'tests/*.py'
echo "== 2 schema 4-limits";        g '"(maximum|maxItems)": 4' -- recommendation/contracts/schemas
echo "== 3 TS 4/5-limits";          g "max\(4\)|Math\.min\(4|result_limit:\s*5|slice\(0,\s*4\)|最多4" -- 'src/*' 'tests/*.ts' 'tests/helpers/*.ts' 'frontend/*'
echo "== 4 ranked prefix";          g "ranked\[: ?request|ranking.\]\[: ?request|ranked or \[\]\)\[:|recorded_ranked_prefix|ranked prefix" -- recommendation
echo "== 5 explicit/implicit";      g "explicit_(score|weight)|implicit_(score|weight)|native:explicit|native:implicit" -- recommendation src frontend
echo "== 6 live prefixes/counts";   g "result_limit,\s*4|slice\(0,\s*[45]\)|,\s*5\]|to_have_count\((5|8)|\.length,\s*5\)" -- src tests frontend scripts evaluation/followup_workflow_browser.py
echo "== 7 native ranking / formulas"; g "native\??\.ranking|1/\(1\+p\.d|0\.8 ×|\.8 explicit" -- src tests frontend docs README.md recommendation/README.md
echo "== 8 v1 feedback in examples"; g "ViewingEvent|feedback_example|\"weighted-v1\"" -- recommendation/examples
echo "== 9 legacy weighted-v1 module"; test ! -e recommendation/ranking/legacy.py && echo "legacy.py absent"; grep -rnE 'ranking[./]legacy|from \.legacy|import legacy|legacy\.[A-Za-z_]+|"weighted-v1' recommendation --include='*.py' --exclude-dir=tests
echo "== 10 not-in-effect markers"; grep -n "not in effect until" recommendation/settings/recommendation.yaml
echo "== end"
