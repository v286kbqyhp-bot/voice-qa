# 独立录音 ASR

本组件读取实际麦克风 WAV，再独立转写。它不把设备自己的 ASR/TTS 字幕当作识别输入，也不给模型预期答案或热词提示。`NativeAdapter.asr=false` 仍表示实时执行器没有内置声学转写；本组件是独立后处理，不能直接成为打断时机门禁。

本次选择 `sherpa-onnx 2.0.1` + 中文 `sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01`。模型约25MiB，CPU运行，Mac ARM64/Python3.13已实测；Windows的安装与实际运行待验证。来源为 [sherpa-onnx官方模型文档](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/zipformer-ctc-models.html)。基础ASR模型不是专门的说话人分离或听感评分器。

## 使用

ASR依赖单独安装到Python3.12+环境，避免改变播放执行器的依赖。模型按官方文档下载到本机 `.local/asr-models/`，不入Git。

```sh
python3.13 -m venv .local/asr-env
.local/asr-env/bin/python -m pip install -r requirements-asr.txt
.local/asr-env/bin/python -m voiceqa.recording_asr \
  runs/<run-id>/audio/mic_raw.wav \
  --model-dir .local/asr-models/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01 \
  --output .local/asr/<run-id>.json
```

Windows使用该环境对应的 `Scripts/python.exe`。此处只有公开模型地址；不需要云端Key。输入为单声道PCM16 WAV。命令不播放、不开麦、不操作设备，已有输出或sealed运行包内的写入目标会被拒绝；原录音保留指纹不变。

## 合同 recording-asr-v1

`RecordedSpeechRecognizer.transcribe(audio: Path) -> dict` 返回 `audio_sha256`、采样率/时长、引擎版本、模型及tokens的SHA256、全文及 `segments[{start_s,end_s,text,speaker,confidence}]`。时间是录音内模型估计位置。`speaker=unknown` 表示未做说话人分离；`confidence=null` 表示接口未提供校准置信度，不填成1或0冒充结论。

输出的 `source=independent_microphone_recording_not_device_caption` 标出证据来源。默认不加增益，不修改音频。判断答案属于设备时，需结合真实播放输入、录音时间窗和当前会话；不能把测试机自身播放的问句当设备答复。小模型有同音错字、漏字，原转写保留，不能静默按日志修成正确答案。可按片段确认实际录到等待词/正文/句尾，再做语义比对；不能据此宣布所有字都完整、零抢话或主观音色通过。

原音频、全文转写留本机，Git仅导出 `records/<id>/asr_summary.json` 的音频/模型/分析指纹和段数。该合同不同于旧 `analysis.schema.json`，不可无映射直接 `import-analysis`。本轮R23的20段录音完成独立转写，另有3秒数字静音对照无识别文本；具体结果以R23反馈为准。
