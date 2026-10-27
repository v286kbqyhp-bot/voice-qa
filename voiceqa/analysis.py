from __future__ import annotations

import json
from pathlib import Path
import uuid
import wave

import jsonschema

from .core import ROOT, digest, dump
from .runner import seal, verify_bundle


def validate_analysis(data, audio: Path):
    schema = json.loads((ROOT / 'schema/analysis.schema.json').read_text(encoding='utf-8'))
    jsonschema.validate(data, schema)
    if digest(audio) != data['audio_sha256']:
        raise ValueError('analysis does not refer to this microphone recording')
    with wave.open(str(audio), 'rb') as f:
        duration = f.getnframes() / f.getframerate()
    for segment in data['segments']:
        if not 0 <= segment['start_s'] <= segment['end_s'] <= duration + 0.01:
            raise ValueError('analysis segment outside recording')
    return data


def import_analysis(bundle: Path, analysis: Path):
    verify_bundle(bundle)
    data = validate_analysis(json.loads(analysis.read_text(encoding='utf-8')), bundle / 'audio/mic_raw.wav')
    name = 'analysis/' + uuid.uuid4().hex + '.json'
    dump(bundle / name, data)
    index = bundle / 'analysis_index.json'
    items = json.loads(index.read_text(encoding='utf-8')) if index.exists() else []
    items.append({'path': name, 'sha256': digest(bundle / name), 'product_status': 'needs_review'})
    dump(index, items)
    seal(bundle)
    return bundle / name
