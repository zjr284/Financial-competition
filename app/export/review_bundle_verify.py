"""Standalone verifier shipped with a private coordinate-review bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def _inside(root, relative):
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or '\\' in relative or ':' in relative:
        raise ValueError('Unsafe bundle path')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('Bundle path escapes its directory')
    return path


def verify_bundle(root, *, restore=False):
    root = Path(root)
    checks = json.loads((root / 'checksums.json').read_text(encoding='utf-8'))
    for name, expected in checks.items():
        path = _inside(root, name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Bundle file missing or checksum mismatch: '+name)
    documents = json.loads((root / 'documents.json').read_text(encoding='utf-8'))
    for document in documents:
        source = _inside(root, document['relative_file'])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != document['sha256']:
            raise ValueError('Original document checksum mismatch')
        if restore:
            target = _inside(root, 'store/blobs/'+digest[:2]+'/'+digest)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError('Existing blob differs from pinned document')
            else:
                shutil.copyfile(source, target)
                target.chmod(0o600)
    return {'verified': True, 'files': len(checks), 'documents': len(documents), 'blobs_restored': restore}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--restore-blobs', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_bundle(args.bundle, restore=args.restore_blobs)))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'verified': False, 'reason': 'Bundle incomplete, unsafe or changed; restore the original archive'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
