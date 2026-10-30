# 外部语音阶段标注

设备日志缺少明确role时，可用本机规则或外部GPT日志评审辅助找到search_bridge/search_body/chat/search_failure。它们是调度代理，不是设备原生事件或真实音频语义证明。

`phase_rules`包含id、role、any_of、all_of、none_of；`phase_review_file`为外部本机JSONL。外部记录需要schema_version、run_id、trace、segment_id、raw_line、text_sha256、role、reviewer、evidence_kind。执行器只接受当前run/trace、匹配行和文本指纹，过期或后续修订字幕不得借用旧role。

用例的wait_speech可配置`phase_role: search_body`，并要求当前说话状态与麦克风活动；命中后仍只返回日志分类+未归属声音活动，不保证搜索正文已从扬声器播放。位置和完整性由独立录音ASR、GPT还原对话及用户观察复核。

原音频、JSONL标注、字幕、评审及回执都在本地data_root。精确50%进度、说话人归属、内部task_id未实现时标未知，不将代理当产品通过。
