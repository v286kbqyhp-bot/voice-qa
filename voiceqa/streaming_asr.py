"""Incremental local subtitles from a growing microphone WAV, in a separate process."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from .recording_asr import fingerprint as digest
from .recording_asr import SherpaRecordedAsr, inspect_recording
from .storage import ROOT, outside_checkout


def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def pcm_header(path):
    """Read RIFF chunks without trusting an in-progress data-size field."""
    with Path(path).open('rb') as f:
        if f.read(4)!=b'RIFF':raise ValueError('ASR input must be RIFF WAV')
        f.read(4)
        if f.read(4)!=b'WAVE':raise ValueError('ASR input must be WAVE')
        fmt=None
        while True:
            chunk=f.read(8)
            if len(chunk)!=8:raise EOFError('WAV header not yet available')
            tag,size=struct.unpack('<4sI',chunk)
            if tag==b'fmt ':
                raw=f.read(size)
                if len(raw)<16:raise EOFError('WAV format not yet available')
                fmt=struct.unpack('<HHIIHH',raw[:16])
            elif tag==b'data':
                if not fmt or fmt[0]!=1 or fmt[1]!=1 or fmt[5]!=16:raise ValueError('stream ASR requires mono PCM16')
                return f.tell(),fmt[2]
            else:f.seek(size+(size%2),1)


def follow(audio,model_dir,folder,stop_file,max_seconds=300):
    folder=outside_checkout(folder);folder.mkdir(parents=True,exist_ok=False)
    output=folder/'live.jsonl';provider=SherpaRecordedAsr(model_dir);rec=provider.rec;stream=rec.create_stream();segments=[];last_text='';cursor=0.;offset=None;rate=None;seq=0
    def emit(kind,**fields):
        nonlocal seq
        seq+=1
        with output.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'sequence':seq,'kind':kind,'observed_monotonic_s':time.monotonic(),**fields},ensure_ascii=False)+'\n')
    def current(at):
        value=rec.get_result_all(stream);stamps=list(value.timestamps);base=float(getattr(value,'start_time',0))
        return value.text,{'segment_id':f'asr-{len(segments)+1:04d}','start_s':round(base+stamps[0] if stamps else max(0,at-.16),3),'end_s':round(min(cursor,base+stamps[-1]+.1 if stamps else at),3),'speaker':'unknown','confidence':None}
    def finish_segment(at):
        nonlocal last_text
        text,meta=current(at)
        if text and meta['start_s']<cursor:
            item={**meta,'text':text};segments.append(item);emit('final',**item)
        rec.reset(stream);last_text=''
    def accept(samples,at):
        nonlocal last_text
        stream.accept_waveform(rate,samples)
        while rec.is_ready(stream):rec.decode_stream(stream)
        text,meta=current(at)
        if text and text!=last_text:emit('partial',**meta,text=text);last_text=text
        if rec.is_endpoint(stream):finish_segment(at)
    emit('started',engine='sherpa-onnx',engine_version=provider.version,model=provider.model_dir.name,source='independent_microphone_stream')
    deadline=time.monotonic()+max_seconds
    while time.monotonic()<deadline:
        try:
            if offset is None:offset,rate=pcm_header(audio)
            with Path(audio).open('rb') as f:
                f.seek(offset);block=f.read(int(rate*.16)*2);block=block[:len(block)//2*2]
            if block:
                offset+=len(block);samples=provider.np.frombuffer(block,dtype='<i2').astype(provider.np.float32)/32768.;cursor+=len(samples)/rate;accept(samples,cursor);continue
            if Path(stop_file).exists():break
        except (FileNotFoundError,EOFError):
            if Path(stop_file).exists():raise ValueError('capture stopped without a readable WAV header')
        time.sleep(.04)
    else:raise TimeoutError('live ASR time budget exceeded')
    if not rate:raise ValueError('no microphone samples received')
    # Drain endpoint context after recording stops. Padding is never written to the original WAV.
    for _ in range(10):accept(provider.np.zeros(int(rate*.16),dtype=provider.np.float32),cursor+1.6)
    stream.input_finished()
    while rec.is_ready(stream):rec.decode_stream(stream)
    text,meta=current(cursor)
    if text and meta['start_s']<cursor:
        item={**meta,'text':text};segments.append(item);emit('final',**item)
    info,_=inspect_recording(audio)
    result={'schema_version':1,'contract':'recording-asr-v1','audio_sha256':info['audio_sha256'],'audio_file':Path(audio).name,'sample_rate':rate,'duration_s':info['duration_s'],'engine':'sherpa-onnx','engine_version':provider.version,'model':provider.model_dir.name,'model_sha256':digest(provider.model),'tokens_sha256':digest(provider.tokens),'source':'independent_microphone_recording_not_device_caption','gain_applied':False,'segments':segments,'text':' '.join(s['text'] for s in segments),'live_subtitles_file':'live.jsonl','limitations':['实时partial可修订，回执保留final及完整JSONL索引','混合录音无说话人分离，confidence未校准','时间是模型估计位置，不是硬件标定首音','不使用字幕/预期答案提示，也不自动给产品PASS']}
    dump(folder/'transcript.json',result);emit('completed',audio_sha256=info['audio_sha256'],segment_count=len(segments));return result


class LiveAsrProcess:
    def __init__(self,audio,folder,config):
        self.folder=outside_checkout(folder);self.stop_file=self.folder.parent/(self.folder.name+'-stop');self.config=config
        if self.folder.exists() or self.stop_file.exists():raise ValueError('ASR output must be new')
        model=Path(config['model_dir']).expanduser().resolve()
        if not (model/'model.int8.onnx').is_file() or not (model/'tokens.txt').is_file():raise ValueError('live ASR model is unavailable; configure asr.model_dir')
        self.folder.parent.mkdir(parents=True,exist_ok=True);self.log=(self.folder.parent/'asr-worker.log').open('w',encoding='utf-8')
        env=dict(os.environ);env['PYTHONPATH']=str(ROOT)
        self.process=subprocess.Popen([str(config.get('python') or sys.executable),'-m','voiceqa.streaming_asr','--audio',str(Path(audio).resolve()),'--model-dir',str(Path(config['model_dir']).expanduser().resolve()),'--output',str(self.folder),'--stop-file',str(self.stop_file),'--max-seconds',str(config.get('max_seconds',300))],cwd=ROOT,env=env,stdout=self.log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+config.get('startup_timeout_s',8)
        while time.monotonic()<deadline:
            if (self.folder/'live.jsonl').is_file():return
            if self.process.poll() is not None:
                self.log.close();raise ValueError('ASR worker failed to initialize; inspect local asr-worker.log')
            time.sleep(.04)
        self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        self.log.close();raise ValueError('ASR startup timeout; capture will be closed without playing test input')
    def finish(self):
        self.stop_file.touch()
        try:
            self.process.wait(timeout=self.config.get('finish_timeout_s',20))
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        finally:self.log.close()
        p=self.folder/'transcript.json'
        if self.process.returncode!=0 or not p.is_file():return {'status':'error','returncode':self.process.returncode,'log':'asr-worker.log','note':'keep raw audio; ASR failure is not a product verdict'}
        d=json.loads(p.read_text(encoding='utf-8'));return {'status':'completed','audio_sha256':d['audio_sha256'],'transcript':'asr/transcript.json','live_subtitles':'asr/live.jsonl','note':'GPT semantic review remains required'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--audio',type=Path,required=True);p.add_argument('--model-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--stop-file',type=Path,required=True);p.add_argument('--max-seconds',type=float,default=300);a=p.parse_args();follow(a.audio,a.model_dir,a.output,a.stop_file,a.max_seconds)

if __name__=='__main__':main()
