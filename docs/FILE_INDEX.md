# 文件索引

- `skills/voice-qa/`：Agent skill、平台启动器与分任务指南。
- `voiceqa/storage.py`：外部本地数据根与输出边界。
- `voiceqa/runner.py`、`batch.py`：YAML动作、采证与失败策略。
- `voiceqa/streaming_asr.py`：独立实时字幕进程；`recording_asr.py`：历史WAV后处理。
- `voiceqa/server_text.py`：实际绑定会话的服务端回复/输入识别原文及分段final，独立于录音ASR。
- `voiceqa/semantic_review.py`、`schema/semantic-review.schema.json`：GPT输入包、还原/判定校验与原模板回执。
- `voiceqa/adapters.py`、`interaction.py`、`lifecycle.py`：音频、设备事件、会话与调度代理。
- `voiceqa/interfaces.py`、`docs/INTERFACES.md`、`docs/openapi.yaml`：组件/HTTP合同与能力。
- `speech-library/`：许可预生成测试输入及精确列表；不含设备回答。
- `cases/`、`fixtures/`、`plans/examples/`、`tests/`：通用示例及离线回归。
- 真实项目用例、来源、录音、回执、GPT评审和摘要：全部在`qa.py paths`显示的本机data_root，不在Git。
