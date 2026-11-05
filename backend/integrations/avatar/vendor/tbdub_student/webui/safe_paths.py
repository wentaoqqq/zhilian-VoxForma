"""Constrain queue media to its configured directories before filesystem access."""
import os
from pathlib import Path


def safe_child(root, value):
    root = Path(root).resolve()
    raw = os.fspath(value)
    if os.path.isabs(raw):
        prefix = str(root).rstrip(os.sep) + os.sep
        if not raw.startswith(prefix):
            raise ValueError('Media path is outside the allowed directory')
        raw = raw[len(prefix):]
    parts = raw.replace(os.sep, '/').split('/')
    clean = []
    for part in parts:
        name = os.path.basename(part)
        if not name or name in ('.', '..') or name != part or '\\' in part:
            raise ValueError('Invalid media path')
        clean.append(name)
    candidate = root.joinpath(*clean).resolve()
    if os.path.commonpath((str(root), str(candidate))) != str(root) or candidate == root:
        raise ValueError('Media path is outside the allowed directory')
    return candidate
