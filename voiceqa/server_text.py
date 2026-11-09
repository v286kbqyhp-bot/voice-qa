"""Literal, trace-bound server captions. These are not acoustic transcripts."""


def _true(value):
    return value is True or isinstance(value, str) and value.lower() == 'true'


def collect_server_text(events, execution, case):
    bindings = {s['name'] for s in case['steps'] + case.get('cleanup', [])
                if s['kind'] == 'wait_event' and s.get('params', {}).get('bind_trace')}
    traces = {}
    for step in execution['steps']:
        actual = step.get('actual', {})
        if step['step'] in bindings and step['execution_status'] == 'completed' and actual.get('trace'):
            traces[actual['trace']] = actual['received_monotonic_s']
    if not traces:
        raise ValueError('server text requires an executed trace binding')
    raw, latest = [], {}
    for event in events:
        trace = event.get('trace')
        if trace not in traces or event.get('received_monotonic_s', -1) < traces[trace]:
            continue
        fields = event.get('fields', {})
        name = event.get('event')
        if name == 'voice.tts_text_changed' and fields.get('source') == 'worker':
            source, speaker = 'server_reply_text', '设备回复'
        elif name == 'voice.asr_text_changed':
            source, speaker = 'device_input_asr', '测试输入'
        else:
            continue
        raw.append(event)
        segment = fields.get('segment_id')
        if not segment or not _true(fields.get('replace')):
            continue  # Unknown/delta semantics must not be silently concatenated.
        key = (trace, source, segment)
        if _true(fields.get('is_final')):
            latest[key] = (event, source, speaker)
    segments = []
    for event, source, speaker in sorted(latest.values(), key=lambda row: row[0]['received_monotonic_s']):
        fields = event['fields']
        if not fields.get('text', '').strip():
            continue
        segments.append({'segment_id': f'text-{len(segments)+1:04d}',
                         'text': fields['text'], 'speaker': speaker, 'source': source,
                         'trace': event['trace'], 'native_segment_id': fields['segment_id'],
                         'received_monotonic_s': event['received_monotonic_s'],
                         'event_id': event.get('event_id'), 'raw_line': event.get('raw_line'),
                         'is_final': True})
    return {'contract': 'server-text-v1', 'traces': list(traces),
            'raw_events': raw, 'segments': segments,
            'limitations': ['服务端回复文本及设备输入识别，不是独立录音ASR或真人实听',
                            '时间是客户端观察时间；不能证明扬声器首音、完整播放、卡顿或真实工具调用']}
