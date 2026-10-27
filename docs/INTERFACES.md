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


## 调试进度与可选就绪事件（2026-10-27）

执行器可通过适配器可选的 `set_observer(callback)` 接收 `wait_started/event_matched/event_rejected/wait_timeout`，不改变既有七方法边界。回调签名为 `callback(event_name, **details)`。Native和Replay可给出逐事件拒绝原因；HTTP客户端只能显示远程等待起止，设备侧逐事件原因须在设备侧日志查看。

能力 `event_alternatives=true` 表示支持明确指定 `alternative_events`。例如主事件input_ready、候选input_ready_fallback、same_trace=true；只接受当前绑定trace的列举事件。不得把所有ready样式日志视为就绪。实际匹配事件保持原名，兼容就绪不会被重命名成正常就绪。

`prepare` 阶段将文本生成PCM16 WAV及资产清单。`run --prepared-assets` 在采集前检查每个步骤的文件、SHA256和准确文本，再将文件交给音频适配器；不会调用文本合成作为缺失回退。HTTP情况下上传准备好的WAV，设备侧同样只播放文件。

本地 `qa.py watch --device-log` 读取运行目录的progress、events和脱敏原始日志，不开启麦克风、不操作设备。每轮运行写入active_run.json供监看器跟随，原始证据在停止采集后生成指纹。终端内容移除控制字符并遮盖敏感字段。

## 批量调度与人工复核包

`python qa.py run-suite <suite.yaml> --mode live --config config.local.yaml --verbose` 按suite.cases的显式顺序执行。每项提供id、file，可提供prepared_assets（本机清单或bindings占位符）、depends_on。真实执行必须有预生成资产；不得在失败重试时临时合成。

策略固定 `diagnostic_repeats: 1`、`continue_after_failure: true`。执行失败/明确oracle未通过触发一次独立采证复测。预置条件阻塞不盲重试；completed但没有oracle为pending_review，不自动报PASS。允许 `initial_run` 引用已完成且源用例指纹和mode相同的运行，配合有reason的initial_verdict登记人工或日志复核决定；不修改原运行。

每次run_manifest.context包含batch_id、attempt_role（initial/diagnostic）、attempt_index、initial_run_id。两次各自独立录音、抓日志并生成指纹，复制到 `batches/<batch_id>/review/<case>/01-initial/` 和 `02-diagnostic/`。`review_index.md`直接链接录音、日志和逐步结果。失败收尾无法获得音频时明确显示未取得。

batch.results.cases包含status（not_passed/blocked/pending_review/passed/skipped_dependency/interrupted）、attempts、review_index。诊断复测结果不覆盖初轮，首次not_passed最终始终not_passed。可注入decision(bundle_path, result)返回结构化status/reason/failure_layer；未配置语义判断器时不凭执行完成推断产品通过。用户停止会停止整个队列。

`python qa.py record-suite batches/<batch_id>`验证完整性并仅登记最小Git摘要，原始采证包留本地。Git hook校验最多一次诊断复测，并拒绝把首轮未通过洗成通过。

## Native 可选扩展：交互等待与会话收尾

本节方法是本地 Python 扩展，见 `InteractionScheduler`，尚未映射为 HTTP v1.0 端点。HTTP 适配器不能仅因为服务器是 Native 就宣称支持这些方法；当前用例会在采集前阻塞。其他设备可独立实现同一合同，不必使用 ADB 或 Mac 音频。

| 方法 | 输入 | 返回 |
|---|---|---|
| `wait_interaction(kind, params)` | `wait_speech` 或 `wait_output`；role、timeout_s；可选 delay_s、max_elapsed_s、quiet_s、text_contains_any | 原始字幕依据、声音活动阈值、调度触发层；不返回产品 PASS |
| `session_status()` | 无；使用当前已绑定会话 | state=`unknown/active/ending/closed/superseded`，trace、reason、原始事件依据 |
| `play_cleanup(asset, params)` | 已准备的资产；idle_finish_before_s、startup_budget_s | 播放起止及预算，或 action=`no_op`、playback_started=false、原因及会话依据 |
| `AudioActivity.levels()` | 无 | rms、updated_monotonic_s、history=`[[stamp,rms],…]`、errors；单调时钟必须与本地执行器可比较 |

`wait_speech` 联合当前 trace 的字面字幕、worker 状态和麦克风活动；声音段首次活动时间锁定，不随短滚动缓冲向后移动。`wait_output` 联合 final 字幕、非 speaking 状态、稳定静音和远端 PCM 统计作为调度代理。二者都不能证明说话者身份、搜索正文类型、物理打断或播放队列清空。观察到会话提前结束立即返回带实际原因的阻塞，不继续空等。

条件收尾示例，仅支持 cleanup：

```yaml
cleanup:
  - kind: audio_play
    name: cleanup_exit
    params:
      text: 不聊了
      if_session_active: true
      idle_finish_before_s: 4.0
      startup_budget_s: 1.0
  - kind: wait_event
    name: cleanup_closed
    params:
      event: ready_next_wake
      after_last_play: true
      if_session_active: true
      timeout_s: 15
```

已结束或正在结束的会话跳过多余语音，保留 `idle_timeout` 等真实原因。仍活跃时，Native 等待同 trace 的 `voice.idle_countdown.running=true`，以 `timeout_ms/remaining_ms` 计算空闲第 4 秒之前播完的预算，扣除 WAV 时长与默认 1 秒播放器启动余量；预算不足记录 `cleanup_deadline_missed` 并等待自然关闭。预算基于主机收到的客户端倒计时，不能证明物理起音或设备在第 4 秒前识别了退出词。G 的主动退出测试仍需独立核对 `reason=user_session_exit`，不能用自然超时代替。

## 实时语义评审接入合同

`SemanticReviewer.review(context, events)` 是供其他 Agent 实现的组件合同，当前未配置自动模型服务。Agent 可增量读取同一次运行的 `events.jsonl` 和 `progress.jsonl`，按 `raw_line` 去重，并携带 case/source SHA256、run_id、trace、当前步骤、完整本轮上下文和明确的验收要求。模型输出应至少包含：

```json
{"verdict":"insufficient_evidence","reason":"待审原因","evidence":[{"raw_line":123,"event":"voice.tts_text_changed"}],"limitations":["仅日志语义；未复听原始录音"]}
```

verdict 仅用 `consistent/nonconform/insufficient_evidence`。判断需引用实际存在的当前会话事件，缺失话术、任务 ID、声学转写或版本事实时不得补写。日志语义判断与声学 `EvidenceAnalyzer` 分开；模型评审异步进行，不能让模型延迟阻塞毫秒级动作调度。完整用例的判定可经批次 `decision` 回调返回；只提供日志评审意见不能自动视为产品通过。本轮人工驱动的 Agent 日志复核见 `R19_FULL_FLOW_20261027.md`，并不代表后台持续 AI 服务已经部署。

## 2026-10-27 D 阶段分类试验

新增 Native 可选参数 `wait_speech.phase_role`，可由本机用例内容规则或外部 Agent JSONL 提供阶段分类；必须保留非原生来源及当前 run/trace/segment/字幕指纹。分类后仍核对播放状态和声音活动，才能触发预生成输入。不按 worker 事件次数推断阶段，也不将字幕文本等同音频进度。协议、两条分类源及限制见 [PHASE_LABELS.md](PHASE_LABELS.md)，实测见 [D_TRIGGER_VALIDATION_20261027.md](D_TRIGGER_VALIDATION_20261027.md)。短暂播放窗口不得等待会话 Agent 同步返回。
