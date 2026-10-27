from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Protocol

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'audio_play', 'audio_record', 'wait_event', 'sleep', 'expect', 'vision_expect'}
FIELDS = [
    '设备SN末8位 / 当前安装版本', '测试人 / 北京时间开始、结束',
    '用例编号或名称', '实际话术与先后顺序', '实际听到的内容',
    '字幕、待机状态、灯光/音量等实际表现', '与普通设备相比是否明显慢（哪一轮）',
    '首次/偶发/必现，重复次数', '对应照片或截图时间',
]


class Blocked(RuntimeError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def append(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


def safe_path(base: Path, name: str) -> Path:
    p = (base / name).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError('path escapes artifact root')
    return p


def load_yaml(path: Path):
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f'duplicate YAML key: {key}')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(path.read_text(encoding='utf-8'), Loader=UniqueLoader)


def load_case(path: Path) -> dict:
    data = load_yaml(path)
    jsonschema.validate(data, json.loads((ROOT / 'schema/case.schema.json').read_text()))
    names = [s['name'] for s in data['steps'] + data.get('cleanup', [])]
    if len(set(names)) != len(names):
        raise ValueError('step names must be unique including cleanup')
    allowed_params = {
        'audio_play': {'file', 'text', 'voice', 'rate'},
        'audio_record': {'max_seconds'}, 'sleep': {'seconds'},
        'wait_event': {'event', 'where', 'timeout_s', 'bind_trace', 'same_trace', 'require_audible_device_speech'},
        'expect': {'contract'}, 'vision_expect': {'contract'},
    }
    for step in data['steps'] + data.get('cleanup', []):
        params = step.get('params', {})
        if set(params) - allowed_params[step['kind']]:
            raise ValueError('unsupported parameters in step: ' + step['name'])
        for key in ('seconds', 'max_seconds', 'timeout_s'):
            if key in params and isinstance(params[key], (int, float)) and not 0 <= params[key] <= 60:
                raise ValueError('step time limit must be 0..60 seconds')
        if step['kind'] == 'wait_event' and not isinstance(params.get('event'), str):
            raise ValueError('wait_event requires an event name')
    return data


def resolve(value, bindings: dict):
    if isinstance(value, str):
        m = re.fullmatch(r'\{\{([^{}]+)\}\}', value)
        if m:
            if m[1] not in bindings:
                raise Blocked(f'unresolved binding: {m[1]}')
            return bindings[m[1]]
        if '{{' in value:
            raise Blocked('only whole-value bindings are supported')
    if isinstance(value, dict):
        return {k: resolve(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, bindings) for v in value]
    return value


class Adapter(Protocol):
    """Methods must raise Blocked for absent capabilities; never fabricate evidence."""

    def capabilities(self) -> dict: ...
    def prepare(self, params: dict, output: Path) -> dict: ...
    def begin(self, output: Path, config: dict) -> dict: ...
    def play(self, asset: dict) -> dict: ...
    def wait_event(self, params: dict) -> dict: ...
    def observe(self, seconds: float) -> dict: ...
    def finish(self) -> dict: ...
