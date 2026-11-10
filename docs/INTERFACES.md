# Voice QA 组件接口

CLI合同`voiceqa-cli-v2`；适配器HTTP仍`/v1`，详细请求/返回以[openapi.yaml](openapi.yaml)为准。核心接口在`voiceqa/interfaces.py`，提供者必须如实声明能力。

| 组件 | 输入 | 输出与边界 |
|---|---|---|
| YAML Runner | 原用例、prepared清单、本机配置 | 步骤/事件/音频封存包，默认外部data_root；completed不等于PASS |
| TTS | 准确text/voice/rate/locale/provider | 预生成PCM16 WAV及SHA；播放阶段不临时合成 |
| AudioIO | 录音、已生成文件播放 | 主机时序、音频、电平；不证明设备收到或说话人身份 |
| Timing | WAV、已播文件、当前trace事件、可选正文分段policy | voiceqa-timing-v1：逐轮工程时延和录音估算，按0.5秒取整，不冒充已校准T5 |
| DeviceEventSource | ADB/连接设备 | 当前trace结构化事件与脱敏原日志；遗漏结构化事件时仍查原log |
| ServerText | 已封存events及实际bind_trace | server-text-v1：worker回复/设备输入ASR、native segment、final和完整原事件；不证明实际声音 |
| RecordedSpeechRecognizer | 独立麦克风WAV | recording-asr-v1原转写、片段、audio/model SHA；未知speaker/未校准confidence |
| LiveAsrProcess | 增长中的本机WAV、ASR环境/模型配置 | asr/live.jsonl实时partial/final，停止后transcript.json；独立进程不占播音调度 |
| SemanticDialogueReviewer | review-request-v1（明确标源的服务端文本或原ASR、上下文、模板和标准） | semantic-review Schema；GPT还原/推测、逐项判定和原模板字段 |
| Receipt | 通过校验的GPT评审 | 本地原模板字段＋标源原文＋GPT还原＋语义判断，不进入Git |

## 运行与能力

启动`qa.py serve --config <local.yaml>`，默认loopback；非loopback需VQA_API_TOKEN环境变量。服务不接收任意shell或ADB可执行路径，设备路由由服务所在机配置。API为capabilities、synthesis/assets、capture start、play、wait-event、observe、stop、analyze；完整结构和限额在OpenAPI。一个采集租约180秒，无续租；跨机单调钟不直接相减。HTTP未承诺Native扩展wait_speech/wait_output、session_status、条件cleanup、UI退出或实时ASR。

Native联合等待只用当前trace、字幕、说话状态、麦克风RMS与PCM计数辅助有界调度，不能当作语义或实际扬声器队列清空的证明。wait_speech支持role、text_contains_any、require_final、require_sound、delay_s、max_elapsed_s；wait_output支持role、text_contains_any、quiet_s、fail_if_body_started、require_caption。进入ending/closed/superseded立即返回session_ended_while_waiting。

正常问答及打断后的wait_output应设置after_input_final:true：等待本次输入final，并排除该final之前已出现的回复segment，防止旧段被截断的迟到final冒充新回答完成。缺少输入final时保留现场并排查/修复/补验，不以静音绕过；该边界仍是客户端观察代理，不能冒充服务端turn/task_id。

wait_event支持明确event/alternative_events、where、text_contains_any、after_last_play、same_trace、bind_trace、timeout_s。`after_last_play`按播放开始的turn_floor限制，避免漏掉文件结束前到达的识别事件；字段缺失不能伪造匹配。全局关闭事件不总有trace，应按本轮绑定和时间窗核对。

device_action只支持exit_voice（真实Android Back），动作完成不等于退出验证；截图和关闭事件另存。cleanup可加if_session_active:true，已关闭不再发词。语音收尾可用idle_finish_before_s≤4与startup_budget_s≤2，预算不足跳过不伪造播放；idle_timeout不是语音退出成功。

## 本地存储与评审

数据根优先VOICEQA_DATA_DIR、config.data_root、本机.local/storage.json，默认~/voice-qa-data；生成物路径必须resolve到仓库外。真实来源/用例、runs、batches、records、reviews、reports都本机保存。许可合成语音库可分发，设备回答/麦克风录音不可入库。Git gate仅验证skill代码和通用fixture，不登记实测结果。

ASR回退配置见config.example.yaml和[ASR](RECORDED_AUDIO_ASR.md)。文件后处理可用独立ASR接口；不能将离线分析冒充实时输出。需要ASR而模型/依赖不可用时先修复或讨论替代路径，不生成占位字幕或直接交付；已验证服务端文本的语义路径不依赖ASR环境。

`review-prepare bundle --template local.yaml --evidence-source server_text`创建文本输入包；recording_asr为兼容旧版的默认，支持`--asr raw.json`。server_text不读取ASR文件，只从实际绑定trace的封存事件取完整final替换快照；未取到时保留缺证并回退录音识别。原partial/final事件保留；每段有source、trace、native_segment_id、event_id、raw_line、received_monotonic_s，接收时间不是发声时间。

当前GPT负责语义评审；模板criterion用evidence_scope=reply_semantics/input_recognition限定可由文本支持的PASS。未标scope及声学/硬件/真人/后台task等项目不能仅凭文本PASS。review-import核原运行与文本一致、片段引用、全部字段和criterion覆盖；模型延迟不进入播音循环。详见[语义回执](SEMANTIC_REVIEW.md)。

`timing <bundle> --output <new-external.json> [--policy local.json]`只读封存包并输出sidecar。Native `run`自动写timing.json。字段包括rows[].step/trace、play_start_monotonic_s/play_end_monotonic_s、ready或ready_fallback的host_observed_delay_s、reply_text.host_observed_delay_s，以及acoustic_estimate.input_end_recording_s/reply_onset_recording_s/response_delay_s/method/uncertainty_s。仅对同一主机时钟求差；先匹配已播音频的有效末尾，再按trace和正文分段限制录音起音候选。原音频SHA与body_segment_ids策略需匹配。参考音频不能定位时返回具体缺项供修复，不编时间。详见[计时指南](../skills/voice-qa/references/timing.md)。

`review-import --final`要求所有适用必测项已经pass/fail。其余状态先排障、补验或与用户讨论，拒绝生成最终回执；普通导入仅生成工作草稿。metadata.user_waivers按criterion id记录reason和authorization，表示用户明确的本轮免验/跳过，不可自行补写。最终功能结果按未豁免的已验项计算。

旧SemanticReviewer(context,events)只针对日志辅助意见；SemanticDialogueReviewer接收明确标源的服务端文本或独立录音字幕，可替换模型提供方。任何provider未提供task_id、实体动作或声学证据时应标未知，不能按预期补结果。
