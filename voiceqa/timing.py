"""Per-turn host observations and same-recording acoustic delay estimates.

No ASR or network is needed. Reference speech envelopes locate the prepared
input in the microphone WAV; trace-bound events restrict response candidates.
Estimates have an explicit method and uncertainty, never fabricated T5 values.
"""
from __future__ import annotations

import array
import json
import math
from pathlib import Path
import statistics
import sys
import wave

from .core import digest, dump, safe_path
from .interaction import is_ack

FRAME_S = .02


def envelope(path):
    with wave.open(str(path), 'rb') as stream:
        if stream.getsampwidth() != 2 or stream.getcomptype() != 'NONE':
            raise ValueError('timing requires PCM16 WAV')
        rate, channels = stream.getframerate(), stream.getnchannels()
        samples = array.array('h', stream.readframes(stream.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    width = max(1, round(rate * FRAME_S)) * channels
    levels = [math.sqrt(sum(v*v for v in samples[i:i+width]) / len(samples[i:i+width]))
              for i in range(0, len(samples), width)]
    return levels, len(samples) / channels / rate


def activity(levels, threshold, start_s=0, end_s=None):
    """Speech-energy spans, merging <=160 ms gaps; not speaker recognition."""
    first = max(0, int(start_s / FRAME_S))
    last = min(len(levels), math.ceil(end_s / FRAME_S)) if end_s is not None else len(levels)
    active = [i for i in range(first, last) if levels[i] > threshold]
    spans = []
    for i in active:
        if spans and i - spans[-1][1] <= 8:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i+1])
    return [(round(a*FRAME_S, 3), round(b*FRAME_S, 3)) for a, b in spans
            if b-a >= 3]


def locate_reference(levels, reference, expected_s):
    """Envelope correlation corrects driver startup/trailing silence offsets."""
    if not reference or not levels or max(reference) == 0:
        return None
    # Log RMS handles gain differences; square-root RMS is a fallback when
    # overlapping device speech masks the reference's quiet syllables.
    matched = None
    for method, transform in [('log_rms',math.log1p),('sqrt_rms',math.sqrt)]:
        ref = [transform(x) for x in reference]
        ref_mean = statistics.mean(ref)
        centered = [x-ref_mean for x in ref]
        norm = math.sqrt(sum(x*x for x in centered))
        if norm == 0:continue
        scores = []
        low = max(0, round((expected_s-1.5)/FRAME_S))
        high = min(len(levels)-len(ref), round((expected_s+1.5)/FRAME_S))
        for shift in range(low, high+1):
            candidate = [transform(x) for x in levels[shift:shift+len(ref)]]
            mean = statistics.mean(candidate)
            scale = math.sqrt(sum((x-mean)**2 for x in candidate))
            score = sum(a*(b-mean) for a,b in zip(centered,candidate))/(norm*scale) if scale else 0
            scores.append((score,shift))
        if not scores:continue
        score,shift=max(scores)
        alternatives=[s for s,i in scores if abs(i-shift)>=15]
        if score >= .65 and (not alternatives or score-max(alternatives)>=.03):
            matched=(score,shift,method)
            break
    if matched is None:return None
    score,shift,method=matched
    spans = activity(reference, max(30, max(reference)*.015))
    if not spans:
        return None
    return {'start_s': round(shift*FRAME_S+spans[0][0], 3),
            'end_s': round(shift*FRAME_S+spans[-1][1], 3),
            'correlation': round(score, 3), 'correlation_method':method,'resolution_s': FRAME_S}


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def _events(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


def build_timing(bundle, policy=None):
    """Works before sealing and as an external sidecar for old sealed runs."""
    bundle = Path(bundle)
    manifest = _read(bundle/'run_manifest.json', {})
    results = _read(bundle/'results.json', {})
    case = {}
    from .core import load_yaml
    if (bundle/'cases.yaml').is_file():
        case = load_yaml(bundle/'cases.yaml')
    params = {s['name']: s.get('params', {}) for s in case.get('steps', [])+case.get('cleanup', [])}
    events = sorted(_events(bundle/'events.jsonl'), key=lambda e: e.get('received_monotonic_s', 0))
    steps = results.get('steps', [])
    plays = [s for s in steps if s['kind'] == 'audio_play' and
             s['execution_status'] == 'completed' and s.get('actual', {}).get('playback_started') is not False
             and 'end_monotonic_s' in s.get('actual', {})]
    bindings = [(s['actual'].get('received_monotonic_s', -math.inf), s['actual'].get('trace'))
                for s in steps if s['execution_status'] == 'completed' and
                params.get(s['step'], {}).get('bind_trace') and s.get('actual', {}).get('trace')]
    audio_file = bundle/'audio/mic_raw.wav'
    device = manifest.get('device', {})
    device = device.get('metadata', device)
    anchor = (manifest.get('capture_result', {}).get('audio', {}).get('capture_clock', {})
              .get('first_sample_monotonic_s', device.get('audio', {}).get('host_monotonic_s')))
    levels, duration = envelope(audio_file) if audio_file.is_file() else ([], 0)
    baseline = sorted(levels)[len(levels)//5] if levels else 0
    threshold = max(45, baseline*3)
    assets = _read(bundle/'tts/manifest.json', {})
    policy = policy or {}
    if policy and policy.get('audio_sha256') != (digest(audio_file) if audio_file.is_file() else None):
        raise ValueError('timing policy belongs to a different recording')
    rows = []
    for index, play in enumerate(plays):
        actual, name = play['actual'], play['step']
        start, end = actual['start_monotonic_s'], actual['end_monotonic_s']
        until = plays[index+1]['actual']['start_monotonic_s'] if index+1 < len(plays) else math.inf
        # A wake can bind while the file is still playing. Questions must use an
        # already bound room; never borrow the next wake's trace.
        wake = params.get(name, {}).get('timing_role') == 'wake' or 'wake' in name.lower()
        bound = [b for b in bindings if b[0] < (until if wake else end)]
        trace = max(bound, default=(None, None))[1]
        relevant = [e for e in events if e.get('trace') == trace and trace and
                    start <= e.get('received_monotonic_s', -1) < until]
        row = {'step': name, 'trace': trace, 'text': play.get('text'),
               'play_start_monotonic_s': start, 'play_end_monotonic_s': end,
               'host_playback_duration_s': round(end-start, 3),
               'role': params.get(name, {}).get('timing_role', 'wake' if wake else 'control' if play.get('phase')=='cleanup' else 'input'),
               'acoustic_estimate': None}
        # Report both ready variants separately. Fallback is not input_ready.
        for event_name, label in [('input_ready', 'ready'), ('input_ready_fallback', 'ready_fallback'),
                                  ('voice.asr_text_changed', 'input_final')]:
            matches = [e for e in relevant if e['event'] == event_name and
                       (label != 'input_final' or e.get('fields', {}).get('is_final') == 'true')]
            if matches:
                event = matches[0]
                row[label] = {'host_observed_delay_s': round(event['received_monotonic_s']-end, 3),
                              'event': event_name, 'raw_line': event.get('raw_line')}
        # Exclude old segments first observed before this input, even if a final
        # for that old segment arrives late after this question.
        texts = [e for e in events if e['event'] == 'voice.tts_text_changed' and
                 e.get('trace') == trace and e.get('fields', {}).get('source') == 'worker']
        first = {}
        for event in texts:
            first.setdefault(event['fields'].get('segment_id'), event['received_monotonic_s'])
        acknowledgments = {e['fields'].get('segment_id') for e in texts if is_ack(e['fields'].get('text', ''))}
        selected = policy.get('steps', {}).get(name, {}).get('body_segment_ids')
        input_finals = [e for e in relevant if e['event'] == 'voice.asr_text_changed'
                        and e.get('fields', {}).get('is_final') == 'true']
        early_finals = [e for e in input_finals if e['received_monotonic_s'] <= end+.75]
        input_floor = (early_finals[-1]['received_monotonic_s'] if early_finals else
                       input_finals[0]['received_monotonic_s'] if input_finals else start)
        candidates = [e for e in relevant if e['event'] == 'voice.tts_text_changed' and
                      e.get('fields', {}).get('source') == 'worker' and e['fields'].get('text') and
                      first.get(e['fields'].get('segment_id'), -1) >= input_floor]
        if selected is not None and (not isinstance(selected, list) or not selected or
                not all(isinstance(s, str) for s in selected) or
                not set(selected) <= {e['fields'].get('segment_id') for e in candidates}):
            raise ValueError('body_segment_ids must select existing current-input trace segments: '+name)
        body = [e for e in candidates if (e['fields'].get('segment_id') in selected if selected is not None
                                        else e['fields'].get('segment_id') not in acknowledgments)]
        response = body[0] if body and row['role'] not in ('control','off') else None
        if candidates:
            row['first_worker_text_delay_s'] = round(candidates[0]['received_monotonic_s']-end, 3)
        if response:
            row['reply_text'] = {'host_observed_delay_s': round(response['received_monotonic_s']-end, 3),
                                 'segment_id': response['fields'].get('segment_id'),
                                 'raw_line': response.get('raw_line'), 'text': response['fields']['text']}
        asset = assets.get(name, {})
        reference_path = bundle/'tts'/f'{name}.wav'
        if anchor is not None and levels and reference_path.is_file():
            if asset.get('sha256') and digest(reference_path) != asset['sha256']:
                raise ValueError('timing reference audio changed: '+name)
            reference, _ = envelope(reference_path)
            located = locate_reference(levels, reference, start-anchor)
            row['input_recording_match'] = located
            if located and response and not wake:
                lower = located['end_s']+.16
                first_segment = candidates[0]['fields'].get('segment_id') if candidates else None
                speaking = [e for e in relevant if e['event'] == 'worker_speaking' and
                            e.get('fields', {}).get('speaking') == 'true' and
                            e['received_monotonic_s'] <= response['received_monotonic_s']]
                if speaking:
                    lower = max(lower, speaking[0]['received_monotonic_s']-anchor-.5)
                if response['fields'].get('segment_id') != first_segment:
                    # Prior segment final marks the approximate spoken boundary,
                    # not the earlier time at which its subtitle was received.
                    prior = [e for e in candidates if e['fields'].get('segment_id') != response['fields'].get('segment_id')
                             and e['received_monotonic_s'] <= response['received_monotonic_s']+.1
                             and e['fields'].get('is_final') == 'true']
                    if prior:
                        lower = max(lower, prior[-1]['received_monotonic_s']-anchor-.5)
                    else:
                        lower = max(lower, response['received_monotonic_s']-anchor-.5)
                upper = min(duration, until-anchor, response['received_monotonic_s']-anchor+1)
                spans = activity(levels, threshold, max(0, lower), upper)
                if spans:
                    onset = spans[0][0]
                    row['acoustic_estimate'] = {'input_end_recording_s': located['end_s'],
                        'reply_onset_recording_s': onset, 'response_delay_s': round(onset-located['end_s'], 2),
                        'method': 'reference_envelope_match_then_trace_bounded_microphone_activity',
                        'uncertainty_s': None, 'report_rounding_s': .5,
                        'speaker_attribution': 'inferred_from_test_schedule_and_trace',
                        'body_segment_id': response['fields'].get('segment_id'),
                        'precision': 'estimate_not_calibrated_T5'}
            if located and wake:
                ready = next((e for e in relevant if e['event'] in ('input_ready','input_ready_fallback')), None)
                if ready:
                    row['wake_to_gate_estimate_s'] = round(ready['received_monotonic_s']-anchor-located['end_s'], 2)
                    row['wake_gate_kind'] = ready['event']
        rows.append(row)
    windows = []
    opened = {}
    if anchor is not None and levels:
        for event in events:
            if event['event'] != 'worker_speaking' or not event.get('trace'):
                continue
            trace = event['trace']
            if event.get('fields', {}).get('speaking') == 'true':
                opened.setdefault(trace, event['received_monotonic_s'])
            elif event.get('fields', {}).get('speaking') == 'false' and trace in opened:
                start = max(0, opened.pop(trace)-anchor)
                end = min(duration, event['received_monotonic_s']-anchor)
                if end <= start:continue
                spans = activity(levels, threshold, start, end)
                gaps = [(spans[i-1][1], spans[i][0]) for i in range(1,len(spans))]
                windows.append({'trace':trace,'start_recording_s':round(start,2),'end_recording_s':round(end,2),
                    'max_internal_low_energy_gap_s':round(max((b-a for a,b in gaps),default=0),2),
                    'long_low_energy_gaps':[[a,b] for a,b in gaps if b-a >= 1],
                    'activity_detected':bool(spans), 'meaning':'recording_energy_not_a_stutter_verdict'})
    return {'contract': 'voiceqa-timing-v1', 'run_id': bundle.name, 'mode': results.get('mode'),
            'audio_sha256': digest(audio_file) if audio_file.is_file() else None,
            'capture_duration_s': round(duration, 3), 'rows': rows, 'speaking_windows':windows,
            'limitations': ['主机接收时间只用于工程时延，同一主机单调钟相减。',
                            '录音值按0.5秒取整汇报；误差上界未标定，混响、人声和连续跨段播报会影响边界。',
                            '实质回复按当前trace文本分段排除等待词；需要时用body_segment_ids复核前导闲聊。',
                            '不使用整段录音长度代替等待，不把字幕时间或音量回调称为T5。']}


def export_timing(bundle, output, policy=None):
    from .runner import verify_bundle
    from .storage import outside_checkout
    bundle = Path(bundle).resolve()
    verify_bundle(bundle, check_receipt_fields=False)
    output = outside_checkout(output)
    if output.is_relative_to(bundle) or output.exists():
        raise ValueError('timing sidecar must be new and outside the sealed run')
    result = build_timing(bundle, policy)
    result['bundle_integrity_sha256'] = digest(bundle/'integrity.json')
    if policy:
        result['body_policy'] = policy
    validate_timing(result, bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    dump(output, result)
    return output


def validate_timing(data, bundle):
    import jsonschema
    from .core import ROOT
    jsonschema.validate(data, json.loads((ROOT/'schema/timing.schema.json').read_text(encoding='utf-8')))
    bundle = Path(bundle)
    audio = bundle/'audio/mic_raw.wav'
    if data['run_id'] != bundle.name or data['audio_sha256'] != (digest(audio) if audio.is_file() else None):
        raise ValueError('timing belongs to a different run/recording')
    if data.get('bundle_integrity_sha256') and data['bundle_integrity_sha256'] != digest(bundle/'integrity.json'):
        raise ValueError('timing belongs to a different sealed run')
    for row in data['rows']:
        estimate = row['acoustic_estimate']
        if row['play_end_monotonic_s'] < row['play_start_monotonic_s']:
            raise ValueError('timing playback clock moved backwards')
        if estimate and not (0 <= estimate['input_end_recording_s'] <= estimate['reply_onset_recording_s'] <= data['capture_duration_s']):
            raise ValueError('acoustic timing outside recording')
    return data
