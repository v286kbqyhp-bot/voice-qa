# Voice QA 组件接口

CLI合同`voiceqa-cli-v2`；适配器HTTP仍`/v1`，详细请求/返回以[openapi.yaml](openapi.yaml)为准。核心接口在`voiceqa/interfaces.py`，提供者必须如实声明能力。

| 组件 | 输入 | 输出与边界 |
|---|---|---|
| YAML Runner | 原用例、prepared清单、本机配置 | 步骤/事件/音频封存包，默认外部data_root；completed不等于PASS |
| TTS | 准确text/voice/rate/locale/provider | 预生成PCM16 WAV及SHA；播放阶段不临时合成 |
| AudioIO | 录音、已生成文件播放 | 主机时序、音频、电平；不证明设备收到或说话人身份 |
| DeviceEventSource | ADB/连接设备 | 当前trace结构化事件与脱敏原日志；遗漏结构化事件时仍查原log |
| RecordedSpeechRecognizer | 独立麦克风WAV | recording-asr-v1原转写、片段、audio/model SHA；未知speaker/未校准confidence |
| LiveAsrProcess | 增长中的本机WAV、ASR环境/模型配置 | asr/live.jsonl实时partial/final，停止后transcript.json；独立进程不占播音调度 |
| SemanticDialogueReviewer | review-request-v1（原ASR、上下文、模板和标准） | semantic-review Schema；GPT还原/推测、逐项判定和原模板字段 |
| Receipt | 通过校验的GPT评审 | 本地原模板字段＋ASR原文＋GPT还原＋语义判断，不进入Git |

## 运行与能力

启动`qa.py serve --config <local.yaml>`，默认loopback；非loopback需VQA_API_TOKEN环境变量。服务不接收任意shell或ADB可执行路径，设备路由由服务所在机配置。API为capabilities、synthesis/assets、capture start、play、wait-event、observe、stop、analyze；完整结构和限额在OpenAPI。一个采集租约180秒，无续租；跨机单调钟不直接相减。HTTP未承诺Native扩展wait_speech/wait_output、session_status、条件cleanup、UI退出或实时ASR。

Native联合等待只用当前trace、字幕、说话状态、麦克风RMS与PCM计数辅助有界调度，不能当作语义或实际扬声器队列清空的证明。wait_speech支持role、text_contains_any、require_final、require_sound、delay_s、max_elapsed_s；wait_output支持role、text_contains_any、quiet_s、fail_if_body_started、require_caption。进入ending/closed/superseded立即返回session_ended_while_waiting。

wait_event支持明确event/alternative_events、where、text_contains_any、after_last_play、same_trace、bind_trace、timeout_s。`after_last_play`按播放开始的turn_floor限制，避免漏掉文件结束前到达的识别事件；字段缺失不能伪造匹配。全局关闭事件不总有trace，应按本轮绑定和时间窗核对。

device_action只支持exit_voice（真实Android Back），动作完成不等于退出验证；截图和关闭事件另存。cleanup可加if_session_active:true，已关闭不再发词。语音收尾可用idle_finish_before_s≤4与startup_budget_s≤2，预算不足跳过不伪造播放；idle_timeout不是语音退出成功。

## 本地存储与评审

数据根优先VOICEQA_DATA_DIR、config.data_root、本机.local/storage.json，默认~/voice-qa-data；生成物路径必须resolve到仓库外。真实来源/用例、runs、batches、records、reviews、reports都本机保存。许可合成语音库可分发，设备回答/麦克风录音不可入库。Git gate仅验证skill代码和通用fixture，不登记实测结果。

启用live ASR见config.example.yaml和[ASR](RECORDED_AUDIO_ASR.md)。文件后处理仍可用独立ASR接口；不能将离线分析冒充实时输出。没有ASR模型或依赖时保留阻塞/未分析，不生成占位字幕。

`review-prepare bundle --template local.yaml [--asr raw.json]`创建本机输入包。当前GPT Agent负责模型评审，不假装有后台API；`review-import review-dir assessment.json`校验输入SHA、字幕片段引用、原模板字段、criterion覆盖，再写本地receipt。模型延迟不进入音频时机循环。细节见[语义回执](SEMANTIC_REVIEW.md)。

旧SemanticReviewer(context,events)只针对日志辅助意见；不能替代以独立录音字幕为主的SemanticDialogueReviewer。后者可换模型服务，仍需保留原ASR与推测的边界。任何provider未提供task_id、说话人、实体动作或声学证据时应标未知，不能按预期补结果。
