"""Verify the original upstream port and explicitly declared downstream patches.

The upstream manifest retains its original fingerprints. A separate patch manifest
records both the base fingerprint and the exact replacement, never an unchecked exception.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'contracts/releases/recommendation-web-feedback-extension.json'


def matches_port(target, expected_hash):
    if not target.is_file():
        return False
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual == expected_hash:
        return True
    if EXTENSION.is_file():
        overlay = json.loads(EXTENSION.read_text(encoding='utf-8'))
        relative = target.relative_to(ROOT).as_posix()
        return any(e['target'] == relative and e['base_ported_sha256'] == expected_hash and e['extended_sha256'] == actual for e in overlay['files'])
    return False
REPLACEMENTS = [
    ("dev.sharedContract.recommendationSubagentContract", "recommendation.contracts"),
    ("dev.recommendationSubagent", "recommendation"),
    ("dev/recommendationSubagent", "recommendation"),
    ("../../../src/model.js", "../../src/model.js"),
    ("dataPreprocessing/tests", "data/tests"),
]
SPECIAL = {
    "repositories/connections.py": [("parents[3]", "parents[2]")],
    "schemas.py": [("Path(__file__).parents[1] / \"sharedContract/recommendationSubagentContract/schemas\"", "Path(__file__).parent / \"contracts/schemas\"")],
    "tests/test_recommendation.py": [("k.startswith('recommendation')", "k.startswith(('recommendation.service', 'recommendation.agent', 'recommendation.repositories', 'recommendation.ranking'))")],
}


def transformed(content, relative):
    for before, after in REPLACEMENTS + SPECIAL.get(relative, []):
        content = content.replace(before, after)
    return content


def downstream_patches(manifest):
    path = ROOT / "contracts/releases/recommendation-downstream-patches.json"
    if not path.exists():
        return {}
    declared = json.loads(path.read_text(encoding="utf-8"))
    if declared.get("upstream_commit") != manifest["upstream_commit"]:
        raise ValueError("Downstream patch names a different upstream version")
    originals = {entry["target"]: entry for entry in manifest["files"]}
    patches = {}
    for patch in declared["files"]:
        target = patch["target"]
        if target in patches or target not in originals:
            raise ValueError("Duplicate or unknown downstream patch target")
        if patch["base_ported_sha256"] != originals[target]["ported_sha256"]:
            raise ValueError("Downstream patch base fingerprint does not match upstream")
        digest = patch["patched_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid downstream patch fingerprint")
        patches[target] = patch
    return patches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "contracts/releases/recommendation-upstream-907cfa8.json"
    manifest = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    patches = downstream_patches(manifest) if manifest else {}
    if args.check and not args.reference:
        for entry in manifest['files']:
            target=ROOT/entry['target']
            expected=patches.get(entry['target'],{}).get('patched_sha256',entry['ported_sha256'])
            if not matches_port(target, expected):
                raise ValueError(f"Upstream port fingerprint changed: {target}")
        print(json.dumps({'mode':'manifest-check','files':len(manifest['files']),'business_logic_changes':len(patches),'declared_downstream_patches':sorted(patches),'contract_source_extension':EXTENSION.is_file()}))
        return
    if not args.reference:
        parser.error('--reference is required for import or original-source comparison')
    if patches and not args.check:
        raise ValueError("Declared downstream patches must be rebased before reimporting upstream")
    if EXTENSION.is_file() and not args.check:
        parser.error('Real-browser contract extension exists; refusing to overwrite it with the original import. Use --check to compare.')
    reference = Path(args.reference)
    records = []
    for source, destination in [
        (reference / "dev/recommendationSubagent", ROOT / "recommendation"),
        (reference / "dev/sharedContract/recommendationSubagentContract", ROOT / "recommendation/contracts"),
    ]:
        for file in sorted(source.rglob("*")):
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            rel = file.relative_to(source).as_posix()
            original = file.read_bytes()
            expected = transformed(original.decode("utf-8"), rel).encode("utf-8")
            target = destination / rel
            if args.check:
                expected_hash=hashlib.sha256(expected).hexdigest()
                patch=patches.get(target.relative_to(ROOT).as_posix())
                if patch and patch['base_ported_sha256']!=expected_hash:
                    raise ValueError(f"Reference differs from declared patch base: {target}")
                if not matches_port(target, patch['patched_sha256'] if patch else expected_hash):
                    raise ValueError(f"Unapproved upstream modification: {target}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected)
            records.append({"source": file.relative_to(reference).as_posix(), "target": target.relative_to(ROOT).as_posix(), "upstream_sha256": hashlib.sha256(original).hexdigest(), "ported_sha256": hashlib.sha256(expected).hexdigest()})
    manifest = {"upstream_commit": "907cfa839d2665b3702450eb40acd4c96fe9296c", "base_commit": "501fb6cdb0aa64bdbabb947324bf845654a9a269", "allowed_substitutions": REPLACEMENTS, "file_specific_substitutions": SPECIAL, "files": records}
    if not args.check:
        output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": "check" if args.check else "import", "files": len(records), "business_logic_changes": len(patches)}))


if __name__ == "__main__":
    main()
