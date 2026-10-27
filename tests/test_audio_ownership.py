import io
import json
import threading
import unittest

from voiceqa.audio_worker import dispatch


class AudioOwnershipTests(unittest.TestCase):
    def backend(self, calls):
        class Backend:
            def start(self, output, config):
                calls.append(('start', threading.get_ident()))
                return {'recording_started': True, 'device': '中文麦克风'}
            def play(self, asset):
                calls.append(('play', threading.get_ident()))
                return {'played': True}
            def stop(self):
                calls.append(('stop', threading.get_ident()))
                return {'valid_capture': True}
        return Backend()

    def test_worker_dispatch_keeps_entire_lifecycle_on_calling_main_thread(self):
        calls = []
        source = io.StringIO('\n'.join(json.dumps({'method':m,'args':args}) for m,args in
                                    [('start',['mic.wav',{}]),('play',[{}]),('stop',[])]))
        output = io.StringIO()
        dispatch(self.backend(calls),source,output)
        self.assertEqual([name for name,_ in calls],['start','play','stop'])
        self.assertTrue(all(owner==threading.get_ident() for _,owner in calls))
        self.assertEqual(len(output.getvalue().splitlines()),3)
        self.assertTrue(output.getvalue().isascii())

    def test_worker_eof_closes_recording_on_owner_thread(self):
        calls=[]
        dispatch(self.backend(calls),io.StringIO('{"method":"start","args":["mic.wav",{}]}\n'),io.StringIO())
        self.assertEqual([name for name,_ in calls],['start','stop'])
        self.assertTrue(all(owner==threading.get_ident() for _,owner in calls))
