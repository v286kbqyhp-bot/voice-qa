# 独立录音ASR

配置`asr.live: true`时，Native运行器录音开始后启动单独解释器跟随WAV，写实时`asr/live.jsonl`（partial/final、序号、主机观察时间、模型估计音频位置）；录音关闭后写`asr/transcript.json`，包含音频/模型SHA与原始片段。该进程不使用设备字幕、期望答案或热词，不改变音频、不控制设备。

ASR依赖单独的Python3.12+环境和`requirements-asr.txt`，核心运行器仍Python3.11+。当前提供sherpa-onnx+中文Zipformer CTC，CPU运行。模型目录需model.int8.onnx与tokens.txt；来源：[官方模型文档](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/zipformer-ctc-models.html)。解释器/权重路径仅在本机配置，不入Git。

已有录音可后处理：`<asr-python> -m voiceqa.recording_asr <WAV> --model-dir <model-dir> --output <new-local.json>`。这不是实时输出；报告应区分实时流与历史文件转写。`RecordedSpeechRecognizer`合同recording-asr-v1保留text、segments、engine/model/audio SHA；speaker未知、confidence未校准不填假分值。

模型错字/漏词保留。GPT依据ASR和上下文做独立推测，在回执另栏标出，不能修改raw_asr。声学时延没有硬件标定；Mac麦克风录音不等于设备麦克风实际输入。原始字幕不能自动PASS；见[语义回执](SEMANTIC_REVIEW.md)。
