"""Bounded scheduling proxies using literal device captions and microphone energy.

No claim of semantic acceptance, speaker identity or hardware playback queue drain.
"""
import time

from .core import Blocked
from .lifecycle import session_status

ACK_PHRASES=('我查查','我来查','我帮你查','我给你查','我查一下','我找找','我搜搜','稍等','帮你查一下','我帮你搜','我来看看')


def compact(text):
    return ''.join(c for c in text if c.isalnum())


def is_ack(text):
    value=compact(text)
    return len(value)<=18 and any(compact(x) in value for x in ACK_PHRASES)


def captions(events,trace,floor,role='any',contains=None):
    values=[]
    for e in events:
        f=e.get('fields',{})
        if e['event']!='voice.tts_text_changed' or e.get('trace')!=trace or e['received_monotonic_s']<floor or f.get('source')!='worker':continue
        text=f.get('text','')
        if not text:continue
        if role=='ack' and not is_ack(text):continue
        if role=='body' and (is_ack(text) or len(compact(text))<6):continue
        if contains and not any(x in text for x in contains):continue
        values.append(e)
    return values


def latest_state(events,trace,floor):
    states=[e for e in events if e['event']=='worker_speaking' and e.get('trace')==trace and e['received_monotonic_s']>=floor]
    return states[-1] if states else None


def wait_interaction(adapter,kind,params):
    if not adapter.logging or adapter.trace is None:raise Blocked('interaction requires a bound device session')
    if not callable(getattr(type(adapter.audio),'levels',None)):raise Blocked('audio levels capability unavailable')
    start=time.monotonic();quiet_start=None;last_observation=0;speech_onsets={}
    timeout=params.get('timeout_s',45);role=params.get('role','any')
    floor=adapter.turn_floor
    adapter._notify('wait_started',kind=kind,expected=params,bound_trace=adapter.trace,
                    evidence_layer='device_log_and_unattributed_sound_activity_proxy')
    while time.monotonic()-start<timeout:
        adapter.logs.healthy()
        now=time.monotonic();events=adapter.logs.all_events();levels=adapter.audio.levels()
        lifecycle=session_status(events,adapter.trace)
        if lifecycle['state'] in ('ending','closed','superseded'):
            adapter._notify('interaction_session_ended',lifecycle=lifecycle)
            raise Blocked('session_ended_while_waiting: '+lifecycle.get('reason',lifecycle['state']))
        if levels.get('errors') or now-levels['updated_monotonic_s']>1:
            raise Blocked('capture stream stale or errored during interaction')
        relevant=captions(events,adapter.trace,floor,role,params.get('text_contains_any'))
        state=latest_state(events,adapter.trace,floor)
        speaking=state and state.get('fields',{}).get('speaking')=='true'
        active=levels['rms']>=adapter.sound_threshold
        if speaking and active:
            key=state['received_monotonic_s']
            if key not in speech_onsets:
                history=[stamp for stamp,rms in levels.get('history',[]) if key<=stamp<=now and rms>=adapter.sound_threshold]
                speech_onsets[key]=min(history) if history else now
        if now-last_observation>=1:
            adapter._notify('interaction_observation',kind=kind,role=role,caption_count=len(relevant),
                            speaking=bool(speaking),rms=round(levels['rms'],2),threshold=adapter.sound_threshold,
                            state_raw_line=state.get('raw_line') if state else None)
            last_observation=now
        if kind=='wait_speech' and relevant:
            current=relevant[-1]
            if params.get('require_final') and current['fields'].get('is_final')!='true':
                time.sleep(0.04);continue
            if not params.get('require_sound',True):
                result={'caption':current,'trigger_layer':'device_caption_only','acoustic_verified':False}
                adapter._notify('event_matched',received=result);return result
            if speaking and active:
                # Latch the first acoustic observation for this speaking span.
                # A short rolling history must not move onset forward every poll.
                onset=speech_onsets[state['received_monotonic_s']]
                elapsed=now-onset
                if elapsed>=params.get('delay_s',0):
                    maximum=params.get('max_elapsed_s')
                    if maximum is not None and elapsed>maximum:
                        raise Blocked('timing_missed: speech onset window passed before dispatch')
                    result={'caption':current,'rms':levels['rms'],'threshold':adapter.sound_threshold,
                            'speech_elapsed_proxy_s':round(elapsed,3),'trigger_layer':'device_caption_and_microphone_activity',
                            'speaker_attribution':'unverified','physical_overlap':'requires recording review'}
                    adapter._notify('event_matched',received=result);return result
        if kind=='wait_output' and relevant:
            body_after_ack=role=='ack' and captions(events,adapter.trace,relevant[-1]['received_monotonic_s'],'body')
            if body_after_ack and params.get('fail_if_body_started'):
                raise Blocked('timing_missed: search body started before acknowledgement gap')
            # Require a final caption and a later non-speaking state, stable mic
            # silence, and no fresh non-silent PCM counter increase.
            final=[e for e in relevant if e['fields'].get('is_final')=='true']
            metrics=[e for e in events if e['event']=='remote_audio' and e.get('trace')==adapter.trace]
            changed=0;previous=None
            for event in metrics:
                value=int(event.get('fields',{}).get('audible_ms','0'))
                if previous is not None and value>previous:changed=event['received_monotonic_s']
                previous=value
            ready=bool(final and state and not speaking and state['received_monotonic_s']>=final[-1]['received_monotonic_s']-0.3 and not active)
            if ready:
                quiet_start=quiet_start or now
                quiet=params.get('quiet_s',1.0)
                if now-quiet_start>=quiet and now-changed>=quiet:
                    texts={e['fields'].get('segment_id',str(e['raw_line'])):e['fields']['text'] for e in final}
                    result={'device_caption_text':' '.join(texts.values()),'quiet_s':round(now-quiet_start,3),
                            'rms':levels['rms'],'threshold':adapter.sound_threshold,
                            'completion_kind':'scheduling_proxy','formal_turn_or_queue_completion':False,
                            'speaker_attribution':'unverified'}
                    adapter._notify('event_matched',received=result);return result
            else:quiet_start=None
        time.sleep(0.04)
    adapter._notify('wait_timeout',kind=kind,expected=params,timeout_s=timeout)
    raise Blocked('execution_timeout: interaction condition not observed; no automatic product verdict')
