# 服务端文本、录音ASR与GPT语义回执

优先验证当轮服务端回复文本，使用`review-prepare <run> --template <local.yaml> --evidence-source server_text`。只选择实际bind_trace的当前会话，worker回复与device_input_asr分别标来源；source为dynamic_current等本地音频不会混入云端回答。保留原始partial/final与native segment_id，不拼接未知delta，也不把某一final当整轮完整。文本缺失或关键段截断时使用下方独立录音ASR路径；已有录音可后处理，无需重放。

server_text模式中，模板criterion需明确`evidence_scope: reply_semantics`（回复文本语义）或`input_recognition`（设备输入识别），并引用相应final，才能凭文本PASS。声音实际播出、断续、首音、实体动作、真人验收、后台task/Tool Calls等标准不能凭文本PASS；默认未标scope也不开放文本PASS。原用例的声学标准不能被拆除，只因文本语义正常就签整例通过。

输出为原模板字段＋服务端原文及设备输入识别＋GPT判断；不生成假ASR或逐字实听。`server-text-v1`保留trace、原segment、事件ID、原始日志行及客户端接收时间；后者不等于物理发声时间。CLI旧默认仍为recording_asr，调用server_text须明确选项，运行配置`asr.live:false`。

## 独立录音ASR回退

运行器负责时序；ASR负责独立录音字幕；当前OpenAI GPT Agent负责带引用的语义还原与判断；确定性代码校验引用/哈希并渲染模板。没有模型API自动调用也可以由调用skill的Agent完成评审，不能伪装后台服务。

## 本机模板

```yaml
fields:
  - 测试时间 / 完整SN / 现场软件版本
  - 用例编号 / 实际话术
  - 实际听到的回答 / 大约等了几秒
  - 字幕、声音、设备动作是否一致
  - 有无断连 / 退出重进后表现
  - 通过、失败或未测 / 截图录屏
values: {}  # 本机已核事实；完整SN/人名仅在本地
metadata:
  tester: 填写测试人
criteria:
  - id: relevant_reply
    description: 逐字采用本用例要求，判断回复是否对应实际输入
```

准备：`review-prepare <run> --template <local.yaml> [--asr <recording-asr-v1.json>]`，输出外部reviews/<run>/<review>/request.json。未指定ASR时读run/asr/transcript.json。该包保留raw_asr及原片段文本，设备端字幕单列为diagnostics，不能替换录音字幕。

GPT按`schema/semantic-review.schema.json`写assessment：request_sha256、reviewer、dialogue、criteria、receipt_fields、summary。每条dialogue引用segment_ids，填speaker、text、certainty与rationale；推测同音词/角色/漏字只在这里。criteria逐条覆盖原标准、引用存在的字幕片段。未知不确定必须写清；已知TTS只能证明测试机播过什么，不能补设备未说的答案。

`review-import <review-dir> <assessment.json>`复核原始包未变、ASR和音频匹配、所有字段和引用存在。产物review.json、receipt.md、review-integrity.json均为本机sidecar，不改封存run。回执先原模板字段，再原始ASR字幕、GPT推测对话、逐项语义判断和证据。

PASS需要真实执行和对应证据引用；server_text按上方明确scope处理。最终交付调用`review-import --final`：所有适用必测项必须为pass/fail，未知或尚未执行的项先查因修复、补测，无法自补则和用户讨论，不交最终回执。无--final仅产生工作草稿。内部兼容的inconclusive/blocked/not_run不是最终交付结论；不能为了通过门禁删原标准或假造验证。

用户明确免验或跳过时，在原模板metadata.user_waivers记录对应criterion id及`reason`、`authorization`原意；最终结论限定实际核验范围。功能、体验、后台核对分别写，缺实听或计时不自动使已证实功能BLOCKED。时间约值由timing.json和原录音支持，不能把收到字幕当实际声音。
