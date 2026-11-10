# Voice QA skill

用YAML和预生成语音驱动真实设备，优先验证并读取服务端回复文本，由当前GPT Agent判断语义并生成本地回执。文本缺失时回退独立录音ASR。Mac/Windows共用编排与接口，录音继续保留，物理声音需各机核验。

仓库只保存skill及其运行所需代码、Schema、示例、测试、接口文档和可复用TTS语音库。实际用例、来源、证据、字幕、GPT评审和回执都留本地；Git不再保存实测摘要或历史回执。

## 安装和入口

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python qa.py install-hooks
./skills/voice-qa/scripts/voice-qa --runtime-info
.venv/bin/python qa.py paths
```

Windows使用`.venv\Scripts\python.exe`及`skills\voice-qa\scripts\voice-qa.cmd`。复制`skills/voice-qa`到Agent的skills目录后，将`VOICEQA_REPO`设为本运行器仓库绝对路径；`VOICEQA_PYTHON`可覆盖Python。相对用例/配置参数以runtime_root为准；仓库外用例用绝对路径。

## 本地数据

`VOICEQA_DATA_DIR`优先，其次配置`data_root`、本机`.local/storage.json`，默认`~/voice-qa-data`。该目录必须在仓库外。用`qa.py paths`查询本机实际位置；复制`config.example.yaml`到忽略的`config.local.yaml`填设备、音频和ASR配置。

```text
<data_root>/
  cases/ sources/ plans/       本机用例、原文和执行计划
  runs/ batches/              不可回写的运行与批次
  work/                      prepared清单、模型配置等本机工作数据
  reviews/<run>/<review>/      GPT输入包、评审和最终receipt.md
  records/ reports/ archive/  本机摘要、历史回执与迁移备份
```

旧录音链接可通过本机忽略快捷链接继续使用；新产物直接写外部目录。删掉Git当前文件不重写Git历史。

## 标准流程

1. 读取用户用例，按原模板准备字段和验收标准；原话术放本地YAML，准确音频优先复用语音库。
2. 用`library-prepare <case> --output <data_root>/work/<new>/`生成清单，`run <case> --mode live --prepared-assets <manifest>`按时机播音、连续录音及抓日志。
3. 当轮探针验证当前trace、worker来源、分段final与替换语义。文本完整时配置`asr.live: false`；缺失时启用独立ASR，或对保留录音后处理。
4. `review-prepare --evidence-source server_text`打包服务端原文、设备输入识别与原始事件；录音ASR路径使用`--evidence-source recording_asr`（为兼容旧调用的默认值）。当前GPT Agent按实际来源评审，不改写原文。
5. 检查每轮`timing.json`，用播放记录、音频有效时长和录音起音补齐约秒值；服务端文本仅支持对应语义/识别项。先处理环境/执行器错误并核验，不把缺实听/计时套成功能BLOCKED。
6. `review-import --final`核验证据和全部适用标准，生成最终本机回执。必测项仍有未知时先修复/补测，无法自补则和用户讨论，不交含“未核验”的最终回执；无--final只是工作草稿。用户明确的跳过/免验需在模板metadata.user_waivers保留授权与原因。

```sh
python qa.py review-prepare /local/runs/RUN --template /local/feedback-template.yaml --evidence-source server_text
# GPT Agent读取返回目录/request.json，依Schema写assessment.json
python qa.py review-import /local/reviews/RUN/REVIEW /local/assessment.json --final
# 旧运行的录音计时补算，不回写封存包
python qa.py timing /local/runs/RUN --output /local/work/NEW/timing.json
```

模板格式和模型评审约束见[语义回执](docs/SEMANTIC_REVIEW.md)，ASR回退见[录音ASR](docs/RECORDED_AUDIO_ASR.md)。执行与复核入口在[Skill](skills/voice-qa/SKILL.md)。有效服务端文本可以替代语义评审所需的独立ASR；`run`执行完成仍不等于产品PASS。不能按预期编造缺失回复，也不能把字幕收到写成实听完整。

## 可替换组件与验证

TTS、AudioIO、设备事件、实时ASR和语义评审分别通过接口连接。[INTERFACES.md](docs/INTERFACES.md)与[openapi.yaml](docs/openapi.yaml)定义现有能力；HTTP当前不承诺本机联合等待或远端流式ASR。语音库见[说明](docs/SPEECH_LIBRARY.md)，全文与片段都不是设备端ASR的替身。

```sh
python -m unittest discover -s tests -q
python qa.py gate
python qa.py run cases/voice-smoke.yaml --mode replay --fixture fixtures/workflow.json
```

replay仅是合成编排验证。hooks核对暂存/推送快照、示例和代码，拒绝实测数据/回执进入Git；不自动提交测试数据。CI覆盖Mac/Windows/Linux离线测试，不能代替各机设备和音频验收。
