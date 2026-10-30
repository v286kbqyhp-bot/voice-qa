---
name: voice-qa
description: "Run YAML-driven voice-device tests with prepared audio, capture independent ASR subtitles, reconstruct dialogue with GPT, judge reply semantics and produce original-template local receipts. Use for voice QA execution and evidence review; ordinary conversation does not need this skill."
metadata:
  version: 0.2.0
---

# Voice QA

执行器按明确时机播预生成音频并录音；独立ASR生成字幕；调用本skill的GPT Agent结合上下文还原对话、判断回复语义；回执保留原模板并并列ASR原文、GPT推测和判断。仓库只提供skill/软件/通用示例/合成语音库，实测资料全部本地仓库外保存。

## 定位和路由

使用本目录`scripts/voice-qa`（Windows为`scripts/voice-qa.cmd`），下称VQA。先`VQA --runtime-info`和`VQA paths`查runtime与外部data_root，读该runtime的AGENTS。复制skill后设置`VOICEQA_REPO`，必要时设置`VOICEQA_PYTHON`；旧CLI合同v1与当前v2不可混配。不要把Mac绝对路径、音频编号搬到其他机器。

- 准备用例、源模板与音频：[prepare](references/prepare.md)。
- 外放、录音、实时字幕：[run](references/run.md)。
- ASR→GPT还原→语义判定→原模板回执：[review](references/review.md)，每个实际用例结束后必做；只有“查日志/取录音”的窄请求不重新测试。
- 本地交付与skill维护：[delivery](references/delivery.md)。

## 关键边界

- 默认按用户当前目标执行，沿用既有授权。ASR检查只判断识别表现，不擅自扩成全量功能验收；硬件控制由用户观察实体效果，不能因云端搜索未就绪而假装播放过控制词。
- YAML只定义动作/等待，不执行任意shell。原话术和语音SHA保留；复用准确匹配的TTS文件，在准备阶段补库，运行中不临时合成。
- 实时字幕的partial可修订，final与完整JSONL保留。独立录音ASR与设备ASR/回复字幕分开。没有字幕不从预期答案编对话；GPT改同音词只写推测栏并引用片段，关键不明则不判通过。
- 语义判断看实际回答是否对应用户输入、是否完成本条预期；设备端ASR与输入不同是诊断线索，不单凭字面差异判后端功能FAIL。完整识别也不等于功能正确。
- 一台设备串行、有界执行，先关房再下一房；时机由执行器控制，GPT评审不占用播音等待循环。失败及复测分目录，初次失败不能洗掉；按用户停止/复测要求和本轮停止条件办。
- 全部用例、来源、录音、日志、字幕、GPT评审、回执和摘要放外部data_root；Git只提交可复用skill改动。旧历史不改写为新结论。

面向用户先给1–3个重要结果和本地回执/录音链接；用户要全文时明确是“原始ASR”还是“GPT还原”，避免混称。
