# ASR与GPT语义复核

1. `verify <run>`核封存包，再读`results.json`确认实际执行到的步骤；没有ASR时先配置/运行独立录音识别，原WAV不修改。ASR实时流保留所有partial/final，最终字幕是模型原文，不能按日志/预期静默修字。
2. `VQA review-prepare /local/run --template /local/original-template.yaml [--asr /local/raw-asr.json]`生成本地request.json和assessment.template.json。输入包绑定运行/音频/ASR SHA，含原模板和criterion；它不是已完成的GPT判断。
3. 当前GPT Agent读取request.json，写独立assessment.json（见runtime `schema/semantic-review.schema.json`）：按片段还原对话，分别标测试输入/设备回复/未知说话人。同音错字可基于语义、已知播放输入和上下文推测，明确`推测还原`与rationale；参考字幕时也写明来源。不确定的词保留不确定，不用产品预期填设备没说出的回答。
4. 每个原criterion给pass/fail/inconclusive/blocked/not_run、理由、ASR片段引用和限制。语义判定看实际回复是否对应输入与预期；设备最终ASR缺词只是辅助线索。未执行/无有效音频/无关键字幕不能PASS，实体动作缺用户观察不能仅据播报通过。已完整识别但错误退出等语义异常应单独判断。
5. 完整填写原模板每个字段；测试人、SN、时间、版本等只取本机事实，未核写未核。然后`VQA review-import /local/review-dir /local/assessment.json`校验并生成receipt.md，回读该文件，确认原始ASR、GPT还原和判定全部并列。

原始字幕与推测永远是两栏。按已知输入进行角色归属属于GPT推断，不冒充说话人分离模型。录音是Mac麦克风时也明确，不推定等于设备麦克风收到的音质。实时JSONL与原始文件SHA保留，评审sidecar不修改已封存运行；修订则新建review。

无云端API Key不阻塞当前Agent做语义复核；不要声称执行器已经后台调用了GPT。将来可替换SemanticDialogueReviewer提供方，但须遵循同样输入/输出及证据边界。
