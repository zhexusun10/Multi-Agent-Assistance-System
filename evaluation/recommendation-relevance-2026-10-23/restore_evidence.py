"""Reassemble and verify the exact Group 42 evidence ZIP from its ordered parts."""
from pathlib import Path
import argparse
import hashlib
import json

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='Destination ZIP; defaults to restored/<original archive name>.')
    parser.add_argument('--verify-only',action='store_true',help='Check every part without creating the ZIP.')
    args=parser.parse_args();base=Path(__file__).resolve().parent
    manifest=json.loads((base/'evidence_parts.json').read_text())
    for part in manifest['parts']:
        p=base/part['name']
        if not p.is_file() or p.stat().st_size!=part['bytes'] or digest(p)!=part['sha256']:
            raise SystemExit('Missing or changed part: '+part['name'])
    if args.verify_only:
        print(f"Verified {len(manifest['parts'])} parts.");return
    output=args.output or base/'restored'/manifest['original_archive_name']
    if output.exists():
        if digest(output)==manifest['original_archive_sha256']:
            print('Existing archive already verified: '+str(output));return
        raise SystemExit('Destination exists with different content; choose another --output path.')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.partial')
    h=hashlib.sha256();size=0
    with temporary.open('xb') as dest:
        for part in manifest['parts']:
            with (base/part['name']).open('rb') as source:
                for block in iter(lambda:source.read(4*1024*1024),b''):
                    dest.write(block);h.update(block);size+=len(block)
    if h.hexdigest()!=manifest['original_archive_sha256'] or size!=manifest['original_archive_bytes']:
        raise SystemExit('Reassembled archive failed verification; partial file retained for diagnosis.')
    temporary.rename(output)
    print('Verified archive: '+str(output))
    print('SHA256: '+h.hexdigest())

if __name__=='__main__':main()
