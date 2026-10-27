"""Audio lifecycle on this process's main thread. JSON lines are private IPC."""
import json
import sys

from .adapters import PcmAudio


def dispatch(backend, source, target):
    active = False
    try:
        for line in source:
            request = {}
            try:
                request = json.loads(line)
                method = request['method']
                if method not in ('start', 'play', 'stop'):
                    raise ValueError('unsupported audio command')
                result = getattr(backend, method)(*request['args'])
                active = method != 'stop'
                response = {'result': result}
            except Exception as exc:
                response = {'error': type(exc).__name__}
            target.write(json.dumps(response, ensure_ascii=False) + '\n')
            target.flush()
            if request.get('method') == 'stop':
                break
    finally:
        if active:
            backend.stop()


if __name__ == '__main__':
    dispatch(PcmAudio(), sys.stdin, sys.stdout)
