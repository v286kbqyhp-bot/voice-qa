# ASR与GPT语义复核

## 已验证服务端文本时优先走此路径

`VQA review-prepare /local/run --template /local/original-template.yaml --evidence-source server_text`。运行器只收集实际执行bind_trace所绑定会话的worker回复与输入ASR；按原segment保留final替换文本，partial/final完整原事件也收入输入包。没有final、缺段或未知替换语义时不补写，必要时回退下方录音ASR。服务端文本模式不读取旧ASR文件。

当前GPT读取request.json，引用text-片段判断文本语义，直接引文标“服务端原文”；推测仍写明依据。模板criterion只有明确`evidence_scope: reply_semantics`或`input_recognition`且有相应final证据，才允许凭文本PASS。未标scope以及声学、硬件、真人、真实工具/任务等项目不能仅凭文本PASS；应保留不确定/阻塞及需补证据。原用例整体PASS仍取决于全部原标准。

`review-import`验证文本确实来自封存事件、运行SHA、引用和全部原模板字段，并渲染“服务端回复文本与设备输入识别（原文）”。它不把文本改名为录音转写，也不宣称实际听到了完整声音。

## 文本不可用时的录音ASR路径

1. `verify <run>`核封存包，再读`results.json`确认实际执行到的步骤；没有ASR时先配置/运行独立录音识别，原WAV不修改。ASR实时流保留所有partial/final，最终字幕是模型原文，不能按日志/预期静默修字。
2. `VQA review-prepare /local/run --template /local/original-template.yaml [--asr /local/raw-asr.json]`生成本地request.json和assessment.template.json。输入包绑定运行/音频/ASR SHA，含原模板和criterion；它不是已完成的GPT判断。
3. 当前GPT Agent读取request.json，写独立assessment.json（见runtime `schema/semantic-review.schema.json`）：按片段还原对话，分别标测试输入/设备回复/未知说话人。同音错字可基于语义、已知播放输入和上下文推测，明确`推测还原`与rationale；参考字幕时也写明来源。不确定的词保留不确定，不用产品预期填设备没说出的回答。
4. 每个原criterion给pass/fail/inconclusive/blocked/not_run、理由、ASR片段引用和限制。语义判定看实际回复是否对应输入与预期；设备最终ASR缺词只是辅助线索。未执行/无有效音频/无关键字幕不能PASS，实体动作缺用户观察不能仅据播报通过。已完整识别但错误退出等语义异常应单独判断。
5. 完整填写原模板每个字段；测试人、SN、时间、版本等只取本机事实，未核写未核。然后`VQA review-import /local/review-dir /local/assessment.json`校验并生成receipt.md，回读该文件，确认原始ASR、GPT还原和判定全部并列。

原始字幕与推测永远是两栏。按已知输入进行角色归属属于GPT推断，不冒充说话人分离模型。录音是Mac麦克风时也明确，不推定等于设备麦克风收到的音质。实时JSONL与原始文件SHA保留，评审sidecar不修改已封存运行；修订则新建review。

旧版封存运行的执行占位回执可能漏长模板字段，不能作为最终回执。review仍核验原证据哈希与逐步记录，按独立原模板生成完整语义sidecar；保留旧占位文件，不覆盖或重新封存原运行。

无云端API Key不阻塞当前Agent做语义复核；不要声称执行器已经后台调用了GPT。将来可替换SemanticDialogueReviewer提供方，但须遵循同样输入/输出及证据边界。
