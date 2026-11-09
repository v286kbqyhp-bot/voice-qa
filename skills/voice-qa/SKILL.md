---
name: voice-qa
description: "Run YAML-driven voice-device tests with prepared audio, review verified server reply text or independent recording ASR with GPT, and produce original-template local receipts. Use for voice QA execution and evidence review; ordinary conversation does not need this skill."
metadata:
  version: 0.3.0
---

# Voice QA

执行器按明确时机播预生成音频并录音。先验证当前设备/会话是否返回服务端回复文本；可用时直接供GPT判断语义，不重复跑独立ASR。文本缺失、截断或来源不明时回退独立录音ASR。回执保留原模板、实际文本来源、GPT评审与证据限制。仓库只提供skill/软件/通用示例/合成语音库，实测资料全部本地仓库外保存。

## 定位和路由

使用本目录`scripts/voice-qa`（Windows为`scripts/voice-qa.cmd`），下称VQA。先`VQA --runtime-info`和`VQA paths`查runtime、review_evidence_sources与外部data_root，读该runtime的AGENTS。server_text需运行器声明支持；旧runtime须同步更新或沿用录音ASR，不能只复制新skill便声称接口可用。复制skill后设置`VOICEQA_REPO`，必要时设置`VOICEQA_PYTHON`；旧CLI合同v1与当前v2不可混配。不要把Mac绝对路径、音频编号搬到其他机器。

- 准备用例、源模板与音频：[prepare](references/prepare.md)。
- 外放、录音、实时字幕：[run](references/run.md)。
- 服务端文本或ASR→GPT语义判定→原模板回执：[review](references/review.md)，每个实际用例结束后必做；只有“查日志/取录音”的窄请求不重新测试。
- 本地交付与skill维护：[delivery](references/delivery.md)。

## 关键边界

- 默认按用户当前目标执行，沿用既有授权。ASR检查只判断识别表现，不擅自扩成全量功能验收；硬件控制由用户观察实体效果，不能因云端搜索未就绪而假装播放过控制词。
- YAML只定义动作/等待，不执行任意shell。原话术和语音SHA保留；复用准确匹配的TTS文件，在准备阶段补库，运行中不临时合成。
- 服务端文本先做当轮探针：核当前trace、source=worker、segment_id、partial/final及替换语义，排除本地欢迎语和旧房。原始事件全部保留；文本完整性按实际段落核，不因有一条final就断言整答已完成。可用后配置asr.live:false，review-prepare使用--evidence-source server_text；缺失时按录音ASR路径复核。
- 服务端回复文本、设备输入ASR、独立录音ASR分开。前两者可判断文本语义/识别结果，不能证明扬声器实播、声学首音、断续、完整播放、真人验收或真实Tool Calls；这些项目继续保留录音/实听/相应遥测，缺证不判通过。不要为了停用ASR而停录音。
- 实时字幕的partial可修订，final与完整JSONL保留。没有有效文本不从预期答案编对话；GPT改同音词只写推测栏并引用片段，关键不明则不判通过。
- 语义判断看实际回答是否对应用户输入、是否完成本条预期；设备端ASR与输入不同是诊断线索，不单凭字面差异判后端功能FAIL。完整识别也不等于功能正确。
- 一台设备串行、有界执行，先关房再下一房；时机由执行器控制，GPT评审不占用播音等待循环。失败及复测分目录，初次失败不能洗掉；按用户停止/复测要求和本轮停止条件办。
- 全部用例、来源、录音、日志、字幕、GPT评审、回执和摘要放外部data_root；Git只提交可复用skill改动。旧历史不改写为新结论。

面向用户先给1–3个重要结果和本地回执/录音链接；用户要全文时明确是“原始ASR”还是“GPT还原”，避免混称。
