#!/usr/bin/env python3
"""Verify preserved pre-cleanup QA bytes. Standard library; no writes or network."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(root):
    root = root.resolve()
    manifest = json.loads((root / 'evaluation/CLEANUP_MANIFEST_EN.json').read_text())
    cache = {}

    def read_location(location):
        if location['kind'] == 'file':
            return (root / location['path']).read_bytes()
        if location['kind'] == 'zip_member':
            key = ('file', location['archive'])
            if key not in cache:
                cache[key] = (root / location['archive']).read_bytes()
        elif location['kind'] == 'split_zip_member':
            key = ('parts', location['parts_manifest'])
            if key not in cache:
                parts_path = root / location['parts_manifest']
                parts = json.loads(parts_path.read_text())
                chunks = []
                for part in parts['parts']:
                    raw = (parts_path.parent / part['name']).read_bytes()
                    assert len(raw) == part['bytes'], part['name']
                    assert digest(raw) == part['sha256'], part['name']
                    chunks.append(raw)
                raw = b''.join(chunks)
                assert len(raw) == parts['original_archive_bytes']
                assert digest(raw) == parts['original_archive_sha256']
                cache[key] = raw
        else:
            raise ValueError('Unsupported location: ' + location['kind'])
        data = cache[key]
        for member in location['members']:
            key = key + (member,)
            if key not in cache:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    cache[key] = archive.read(member)
            data = cache[key]
        return data

    for item in manifest['original_files']:
        data = read_location(item['preserved_at'])
        assert len(data) == item['bytes'], item['original_path']
        assert digest(data) == item['sha256'], item['original_path']
        git_blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        assert git_blob == item['git_blob_sha1'], item['original_path']
    for item in manifest['current_files']:
        data = (root / item['path']).read_bytes()
        assert len(data) == item['bytes'], item['path']
        assert digest(data) == item['sha256'], item['path']
    print(json.dumps({
        'result': 'PASS',
        'source_main': manifest['source_main'],
        'original_files_preserved': len(manifest['original_files']),
        'current_files_verified': len(manifest['current_files']),
        'original_whole_archives_unchanged': manifest['summary']['whole_archives_retained'],
        'original_split_parts_unchanged': manifest['summary']['split_parts_retained'],
        'model_calls': 0,
    }, indent=2))


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
