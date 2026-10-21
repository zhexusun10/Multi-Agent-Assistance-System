"""Declared fixes retain the upstream fingerprint and reject undeclared changes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("change", ["declared", "unlisted-edit", "wrong-base", "reverted-patch"])
def test_downstream_patch_fingerprints_remain_enforced(tmp_path, change):
    root = Path(__file__).parents[1]
    script = tmp_path / "scripts/import-recommendation-upstream.py"
    script.parent.mkdir()
    shutil.copy2(root / "scripts/import-recommendation-upstream.py", script)
    release = tmp_path / "contracts/releases"
    release.mkdir(parents=True)
    target = tmp_path / "recommendation/agent/controller.py"
    target.parent.mkdir(parents=True)
    before, after = b"original controller\n", b"declared controller fix\n"
    digest = lambda b: hashlib.sha256(b).hexdigest()
    manifest = {"upstream_commit": "pinned-upstream", "files": [{"target": "recommendation/agent/controller.py", "ported_sha256": digest(before)}]}
    original_path = release / "recommendation-upstream-907cfa8.json"
    original_bytes = json.dumps(manifest).encode()
    original_path.write_bytes(original_bytes)
    patch = {"upstream_commit": "pinned-upstream", "files": [{"target": "recommendation/agent/controller.py", "base_ported_sha256": digest(before), "patched_sha256": digest(after)}]}
    if change == "wrong-base":
        patch["files"][0]["base_ported_sha256"] = digest(b"another upstream")
    (release / "recommendation-downstream-patches.json").write_text(json.dumps(patch))
    target.write_bytes(before if change == "reverted-patch" else after + (b"unlisted edit" if change == "unlisted-edit" else b""))
    result = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert (result.returncode == 0) == (change == "declared")
    assert original_path.read_bytes() == original_bytes
    if change == "declared":
        assert json.loads(result.stdout)["business_logic_changes"] == 1
