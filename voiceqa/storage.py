"""Machine-local test data; the checkout contains only reusable skill software."""
import json
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def outside_checkout(path, root=ROOT):
    path=Path(path).expanduser().resolve()
    if path==root.resolve() or path.is_relative_to(root.resolve()):
        raise ValueError('test artifacts must be outside the skill checkout; set VOICEQA_DATA_DIR')
    return path


def data_root(config=None):
    configured=os.environ.get('VOICEQA_DATA_DIR') or (config or {}).get('data_root')
    marker=ROOT/'.local/storage.json'
    if not configured and marker.is_file():configured=json.loads(marker.read_text(encoding='utf-8')).get('data_root')
    path=Path(configured).expanduser() if configured else Path.home()/'voice-qa-data'
    if not path.is_absolute():raise ValueError('data_root must be an absolute machine-local path')
    return outside_checkout(path)


def output_path(explicit, category, config=None):
    return outside_checkout(explicit) if explicit is not None else data_root(config)/category
