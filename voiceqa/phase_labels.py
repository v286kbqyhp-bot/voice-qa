"""External semantic labels are advice about literal captions, never native telemetry."""
import hashlib
import json
from pathlib import Path

from .core import Blocked, append

ROLES={'search_bridge','search_body','chat','search_failure'}


def rule_label(adapter, events, role):
    """Case-specific, synchronous text classification; never claims native role."""
    rules=getattr(adapter,'phase_rules',[])
    for event in reversed(events):
        fields=event.get('fields',{})
        if (event['event']!='voice.tts_text_changed' or event.get('trace')!=adapter.trace or
                event['received_monotonic_s']<adapter.turn_floor or fields.get('source')!='worker'):continue
        # Only the current caption segment is eligible; no older-role fallback.
        text=fields.get('text','')
        for rule in rules:
            if rule.get('role')!=role:continue
            if (all(x in text for x in rule.get('all_of',[])) and
                    any(x in text for x in rule.get('any_of',[])) and
                    not any(x in text for x in rule.get('none_of',[]))):
                label={'schema_version':1,'run_id':adapter.output.name,'trace':adapter.trace,
                       'segment_id':fields.get('segment_id'),'raw_line':event['raw_line'],
                       'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'role':role,
                       'reviewer':'local_case_text_rule','evidence_kind':'rule_text_classification',
                       'rule_id':rule.get('id'),'rule':rule,'native_role_verified':False}
                return event,label
        return None
    return None


def matching_label(adapter, events, role):
    if getattr(adapter,'phase_rules',None):
        return rule_label(adapter,events,role)
    source=getattr(adapter,'phase_review_file',None)
    if role not in ROLES or source is None:
        raise Blocked('external phase review capability unavailable')
    source=Path(source)
    if not source.is_file():raise Blocked('external phase review file unavailable')
    if source.stat().st_size>1_000_000:raise Blocked('external phase review exceeds size limit')
    labels=[]
    for line in source.read_text(encoding='utf-8').splitlines():
        try:labels.append(json.loads(line))
        except (ValueError,TypeError):continue  # A writer may not have completed its last line.
    for label in reversed(labels):
        if not isinstance(label,dict) or label.get('schema_version')!=1:continue
        if label.get('run_id')!=adapter.output.name or label.get('trace')!=adapter.trace or label.get('role')!=role:continue
        for event in reversed(events):
            fields=event.get('fields',{})
            if (event['event']!='voice.tts_text_changed' or event.get('trace')!=adapter.trace or
                    event['received_monotonic_s']<adapter.turn_floor or fields.get('source')!='worker'):continue
            if (label.get('raw_line')!=event['raw_line'] or label.get('segment_id')!=fields.get('segment_id') or
                    label.get('text_sha256')!=hashlib.sha256(fields.get('text','').encode()).hexdigest()):continue
            if not label.get('reviewer') or label.get('evidence_kind')!='semantic_log_review':continue
            # Reject an old segment's label once a newer caption segment has appeared.
            later=[e for e in events if e['event']=='voice.tts_text_changed' and e.get('trace')==adapter.trace and
                   e['received_monotonic_s']>event['received_monotonic_s'] and e.get('fields',{}).get('source')=='worker' and
                   e.get('fields',{}).get('segment_id')!=fields.get('segment_id')]
            if later:continue
            return event,label
    return None


def preserve_label(adapter, label):
    append(adapter.output/'phase_labels.jsonl',label)
