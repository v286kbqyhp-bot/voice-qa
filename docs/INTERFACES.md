# 语音测试适配接口 v1.0

执行器只读取用例和结构化返回值，不依赖 macOS、ADB、特定 TTS 或某个模型。`voiceqa/core.py::Adapter` 是执行器边界，`voiceqa/interfaces.py` 定义可独立替换的组件；HTTP 实现遵守 `openapi.yaml`。

## 组件与职责

| 组件 | 输入 | 返回/产物 | 当前参考实现 |
|---|---|---|---|
| TtsProvider | 原文、voice、rate | PCM16 WAV、SHA256、时长 | MacSay、WindowsSapi；也可直接传 WAV 或远端合成 |
| AudioIO | 输入/输出设备、采样率、WAV | 连续录音、播放起止观测、丢帧与错误 | sounddevice连续输入；Mac默认afplay输出，其他平台PortAudio，播放器可独立替换 |
| DeviceEventSource | 本机设备配置 | 原始日志位置、接收单调时钟、事件、trace | ADB 定向 logcat；可注入串口/其他日志实现 |
| Adapter | 资产、会话、等待与观察参数 | JSON 元数据、动作结果和证据包 | NativeAdapter / HttpAdapter / ReplayAdapter |
| EvidenceAnalyzer | 原始录音、事件、运行信息 | `schema/analysis.schema.json` | 接口已定义；中文 ASR、说话者与语义模型尚未配置 |
| Report/Git | 用例、实际动作、证据 | 逐步 JSON、九字段回执、交付清单、Git 记录 | 本仓库实现 |

能力声明表示实现具备的接口，设备就绪需另做 `doctor` 与实际探针。`tts=false` 时可上传预制 WAV。`asr=false` 或 `speaker_attribution=false` 时，调用方必须保留待复核，不能把日志文本填成“实际听到”。

## 启动与连接

```bash
# 在设备所在电脑启动；本机路由信息仅存在该机的 Git-ignored 配置中
python qa.py serve --config config.local.yaml --host 127.0.0.1 --port 8765
# 另一进程/Agent 运行相同 YAML
python qa.py run cases/voice-smoke.yaml --mode http --adapter-url http://127.0.0.1:8765
```

默认只监听 loopback。局域网监听必须设置 `VQA_API_TOKEN`，客户端从同名环境变量读取，并发送 `Authorization: Bearer …`。跨不可信网络应由部署方提供 HTTPS 或 SSH 隧道。服务不会接收远程 shell、ADB 可执行路径或主机文件路径；ADB 路由由服务端配置。不要将 Token 写进 YAML/Git。

## 调用顺序

1. `GET /v1/capabilities` 查询能力。
2. 对全部话术调用 `POST /v1/assets`，提前生成/上传 WAV。
3. `POST /v1/sessions` 开始连续录音及可选设备日志。
4. 交替调用 `play`、`wait-event`、`observe`，每一步保存原始返回。
5. 无论成功还是失败，`POST …/stop` 结束采集并取得带 SHA256 的证据文件。
6. 可选 `POST …/analyze`；未配置分析器会明确阻塞。外部分析器也可以直接返回 analysis schema，由 `import-analysis` 导入本地证据包。
7. 生成回执，`verify` 检查完整性，`record` 登记到 Git，再 commit/push 触发 hook。

原生音频运行在独立子进程的主线程，通过私有JSON行IPC接受start/play/stop；HTTP请求线程不直接操作驱动。每次驱动操作有超时，超时会回收子进程并保留不完整采集。

一个服务同时只允许一个采集会话。租约为 180 秒，防止客户端丢失后无限录音；单个等待/观察最多 60 秒。租约到期会收尾，正在执行的有界调用可能使收尾延后最多一个调用。长批次按独立用例拆分；当前未实现续租。

## 请求与返回

### 能力

```json
{"api_version":"1.0","adapter":"native","tts":true,"audio_capture":true,"audio_play":true,"device_events":"adb","asr":false,"speaker_attribution":false,"verified_search_body_event":false}
```

### 资产（不播放）

`POST /v1/assets`，输入二选一：

```json
{"text":"你好，请用一句话介绍自己。","voice":"Tingting","rate":175}
```

```json
{"wav_base64":"<PCM16 WAV 的 Base64>"}
```

`voice`、`rate` 的含义由合成适配器声明；Mac rate 为 words/min，Windows SAPI 当前使用系统默认速率，不做等值转换。响应 201：

```json
{"asset_id":"<32位hex>","sha256":"<64位hex>","duration_s":2.1,"sample_rate":16000,"channels":1,"wav_base64":"<WAV内容>"}
```

调用方必须核对返回音频 SHA256；正文是资产元数据，不代表设备已经听清。Base64 是 v1 的便携实现，单请求上限 16 MiB；大文件分块/对象存储接口未实现。

### 采集会话

`POST /v1/sessions`：

```json
{"config":{"require_device":true,"input_device":0,"output_device":1,"sample_rate":16000,"operator":"易子钧"}}
```

返回 201：`{"session_id":"<32位hex>","metadata":{…设备版本与音频信息…},"lease_s":180}`。`input_device`、`output_device` 是服务端设备编号，使用该机 `doctor` 查询，不把 Mac 编号照抄到 Windows。ADB transport 与 cloud SN 不互相推导；cloud SN 未核对时字段标为待核对。

### 播放

`POST /v1/sessions/{session_id}/play`：`{"asset_id":"…","request_id":"可选调用方唯一ID"}`。

返回：`start_monotonic_s`、`end_monotonic_s`、`asset_sha256`、`timing_kind`。这些是同一服务进程的主机流观测，不是物理扬声器起音。跨机器的单调时钟不能直接相减。相同 request_id 在同一会话最多执行一次；失败后返回不确定并禁止同 ID 重播。未传 request_id 的直接调用不具备去重语义。HttpAdapter 会自动生成 request_id，且不会自动重试网络异常。

### 等事件

`POST …/wait-event`：

```json
{"event":"input_ready","timeout_s":20,"bind_trace":true}
```

```json
{"event":"worker_speaking","same_trace":true,"where":{"speaking":"false"},"timeout_s":30}
```

返回：`event`、`tag`、`trace`、`fields`、`received_monotonic_s`、`raw_line`、`evidence_layer: device_log`。`bind_trace` 为后续 `same_trace` 绑定会话。未绑定时 `same_trace` 不会匹配。原始采集日志位置可回溯；适配器不把 `worker_speaking` 当作整轮搜索正文结束，不推断 task_id。需要可靠搜索正文事件或声学说话者触发时，当前参考适配器返回阻塞。

### 静默观察

`POST …/observe`：`{"seconds":10}`。返回实际观察时长。此阶段不播放音频，但仍持续采集；静默动作完成不代表“没有补播旧结果”的语义断言成立，后者需分析录音/任务事件。

### 结束与取证

`POST …/stop`：`{}`。重复调用返回相同采证结果。响应含 `audio` 的采样数、峰值、错误、`valid_capture`，以及：

```json
{"files":[{"path":"audio/mic_raw.wav","sha256":"…","base64":"…"},{"path":"events.jsonl","sha256":"…","base64":"…"}]}
```

收尾失败仍返回已取得的证据，包含`capture_error`和`audio.valid_capture=false`，会话关闭，不能记为通过。

调用方要限制相对路径、验证 SHA256，保留原始音频。`valid_capture` 只说明录到了非全零数据且未记录采集错误，不证明录到了指定设备的声音。日志在采集时脱敏，文件名明确为 `device_redacted.log`，不是未处理的原始日志。

### 分析器

`POST …/analyze`：仅可在 stop 后调用，已注入 `analyzer` 的服务返回 analysis schema。默认适配器 `asr=false`，返回 409 `CAPABILITY_BLOCKED`。

分析输出包含 `audio_sha256`、`segments[{start_s,end_s,speaker,text,confidence,overlap}]`、`observations` 和 `limitations`。speaker 只能是 tester/device/other/unknown，听不清用 text=null。`python qa.py import-analysis <run目录> <analysis.json>` 核验音频指纹与时间范围，新增分析文件，不覆盖原始证据，不自动授予产品 PASS。

## 状态与错误

| HTTP | code | 调用方动作 |
|---|---|---|
| 400 | INVALID_REQUEST | 修正字段、格式或范围 |
| 401 | UNAUTHORIZED | 检查环境 Token；不回显凭据 |
| 404 | NOT_FOUND | 核对会话与接口版本 |
| 409 | CAPABILITY_BLOCKED | 保存原因并阻塞依赖步骤，不能记产品 FAIL |
| 413 | BODY_TOO_LARGE | 缩短用例或使用未来大文件适配器 |
| 500 | ADAPTER_ERROR | 保存执行异常，停止自动重试 |

执行状态 completed/blocked/error/not_executed，产品状态 needs_review/not_applicable/log_observed_only，证据状态单独记录。v1 尚未提供自动产品 PASS/FAIL 判定。不同设备、版本、运行或重试的结果不得拼成一次 PASS。

## 其他 Agent 的最小实现

实现 Adapter 的七个方法，或实现 OpenAPI 中端点；运行 `tests/test_http.py` 作为合同示例。Python 可以直接注入组件：

```python
from voiceqa.adapters import NativeAdapter
from voiceqa.service import AdapterServer

factory = lambda: NativeAdapter(tts=MyTtsProvider(), audio=MyAudioIO(), event_source=MyDeviceEvents())
server = AdapterServer(('127.0.0.1', 8765), root, adapter_factory=factory)
```

其他语言无需使用本仓库 Python，保持 JSON 结构、证据层级、错误语义和时钟边界即可。未实现能力必须如实声明。Windows TTS、Linux TTS、串口、USB 音频、云端 ASR 可独立替换，不修改 YAML 编排器。
