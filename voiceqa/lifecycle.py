"""Literal lifecycle observations, scoped to the last bound session start."""


def cleanup_budget(event, now, duration_s, finish_before_s=4, startup_budget_s=1):
    fields=event['fields']
    timeout=float(fields['timeout_ms'])/1000
    remaining=float(fields['remaining_ms'])/1000
    deadline=event['received_monotonic_s']+remaining-(timeout-finish_before_s)
    estimated=duration_s+startup_budget_s
    return {'countdown_raw_line':event['raw_line'],'finish_before_idle_s':finish_before_s,
            'finish_deadline_monotonic_s':deadline,'available_s':round(deadline-now,3),
            'asset_duration_s':duration_s,'startup_budget_s':startup_budget_s,
            'estimated_playback_s':estimated,'can_finish_in_budget':now+estimated<=deadline,
            'timing_layer':'device_countdown_received_on_host_not_physical_audio'}


def session_status(events, trace):
    starts = [e for e in events if e['event'] == 'started']
    bound = [e for e in starts if trace and e.get('trace') == trace]
    if not bound:
        return {'state': 'unknown', 'trace': trace}
    start = bound[-1]
    if starts[-1] is not start:
        return {'state': 'superseded', 'trace': trace, 'evidence': starts[-1]}
    state = {'state': 'active', 'trace': trace, 'evidence': start}
    for event in events:
        if event['received_monotonic_s'] < start['received_monotonic_s']:
            continue
        if event.get('trace') not in (None, trace):
            continue
        if event['event'] == 'session_end_requested':
            state = {'state': 'ending', 'trace': trace,
                     'reason': event.get('fields', {}).get('reason', 'unspecified'),
                     'evidence': event}
        elif event['event'] == 'ready_next_wake':
            state = {**state, 'state': 'closed', 'closed_evidence': event}
    return state
