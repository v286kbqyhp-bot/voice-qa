"""Optional local ASR of recorded WAVs; never consumes device captions as prompts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import wave


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_recording(path):
    path=Path(path).resolve()
    with wave.open(str(path),'rb') as source:
        if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getcomptype()!='NONE':
            raise ValueError('recording ASR requires mono PCM16 WAV')
        rate=source.getframerate();frames=source.getnframes()
        if rate<=0 or frames<=0:raise ValueError('recording is empty')
        data=source.readframes(frames)
        if len(data)!=frames*2:raise ValueError('recording payload is truncated')
    return {'path':path,'sample_rate':rate,'duration_s':frames/rate,'audio_sha256':fingerprint(path)},data


def validate_destination(audio,output):
    from .storage import outside_checkout
    audio=Path(audio).resolve();output=outside_checkout(output)
    if output.exists():raise FileExistsError('ASR output must be new; preserve earlier analysis')
    if output==audio:raise ValueError('ASR output cannot overwrite the recording')
    for parent in audio.parents:
        if (parent/'run_manifest.json').exists() and (parent/'integrity.json').exists() and output.is_relative_to(parent):
            raise ValueError('ASR output must remain outside the sealed run bundle')
    return output


class SherpaRecordedAsr:
    """An offline file consumer using an online decoder; no microphone or playback."""
    def __init__(self,model_dir,threads=2):
        import numpy as np
        import sherpa_onnx
        self.np=np;self.version=sherpa_onnx.__version__;self.model_dir=Path(model_dir).resolve()
        self.model=self.model_dir/'model.int8.onnx';self.tokens=self.model_dir/'tokens.txt'
        if not self.model.is_file() or not self.tokens.is_file():raise ValueError('ASR model or tokens missing')
        self.rec=sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            tokens=str(self.tokens),model=str(self.model),num_threads=threads,sample_rate=16000,feature_dim=80,
            enable_endpoint_detection=True,rule1_min_trailing_silence=1.2,rule2_min_trailing_silence=.6,
            rule3_min_utterance_length=15,decoding_method='greedy_search',provider='cpu')

    def decode(self,samples,rate):
        stream=self.rec.create_stream();segments=[];last_end=0;duration=len(samples)/rate
        def flush(cursor):
            nonlocal last_end
            result=self.rec.get_result_all(stream)
            stamps=list(result.timestamps);base=float(getattr(result,'start_time',last_end))
            start=base+stamps[0] if stamps else last_end
            end=base+stamps[-1]+.1 if stamps else cursor
            if result.text and start<duration:
                segments.append({'start_s':round(max(0,start),3),'end_s':round(min(duration,end),3),
                    'text':result.text,'speaker':'unknown','confidence':None})
            last_end=cursor
        step=int(.16*rate)
        padded=self.np.concatenate([samples,self.np.zeros(int(rate*1.4),dtype=self.np.float32)])
        for offset in range(0,len(padded),step):
            stream.accept_waveform(rate,padded[offset:offset+step])
            while self.rec.is_ready(stream):self.rec.decode_stream(stream)
            if self.rec.is_endpoint(stream):flush((offset+step)/rate);self.rec.reset(stream)
        stream.input_finished()
        while self.rec.is_ready(stream):self.rec.decode_stream(stream)
        flush(len(padded)/rate)
        return segments

    def transcribe(self,audio):
        metadata,data=inspect_recording(audio);started=time.monotonic()
        samples=self.np.frombuffer(data,dtype='<i2').astype(self.np.float32)/32768.0
        segments=self.decode(samples,metadata['sample_rate'])
        return {'schema_version':1,'contract':'recording-asr-v1','audio_file':metadata['path'].name,
            **{k:metadata[k] for k in ('audio_sha256','sample_rate','duration_s')},
            'engine':'sherpa-onnx','engine_version':self.version,'model':self.model_dir.name,
            'model_sha256':fingerprint(self.model),'tokens_sha256':fingerprint(self.tokens),
            'inference_seconds':round(time.monotonic()-started,3),'gain_applied':False,
            'source':'independent_microphone_recording_not_device_caption','segments':segments,
            'text':' '.join(s['text'] for s in segments),
            'limitations':['混合录音无说话人分离；speaker为unknown',
                'confidence为空表示接口未提供校准置信度；错字、漏字需保留不确定',
                '时间为录音内模型估计位置，不是已校准的物理首音',
                '未使用设备字幕、预期答案或热词作输入提示；不自动返回产品PASS']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio',type=Path);parser.add_argument('--model-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args();dest=validate_destination(args.audio,args.output)
    result=SherpaRecordedAsr(args.model_dir,args.threads).transcribe(args.audio)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(dest),'audio_sha256':result['audio_sha256'],'segments':len(result['segments'])}))


if __name__=='__main__':main()
