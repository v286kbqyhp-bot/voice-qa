# 文件索引

路径相对仓库根目录。唯一规则入口是 [AGENTS.md](../AGENTS.md)，命令见 [README.md](../README.md)。原始证据目录被 Git 忽略，clone 后没有历史原始录音属于预期行为。

## 按目的找文件

| 要做什么 | 先看哪里 |
|---|---|
| 跑 YAML、实时看步骤 | `qa.py` → README 的 library-prepare/run/watch |
| 找准确话术并直接下载 WAV | [speech-library/list.yaml](../speech-library/list.yaml)、[SPEECH_LIBRARY.md](SPEECH_LIBRARY.md) |
| 对接其他设备或 Agent | [INTERFACES.md](INTERFACES.md)、[openapi.yaml](openapi.yaml)、`voiceqa/interfaces.py` |
| 排查没播放、超时、误触发 | [PITFALLS.md](PITFALLS.md)、本机 progress.jsonl、steps/、脱敏日志 |
| 看首次失败与诊断复测 | 本机 batches/<id>/report.md、review/<case>/review_index.md |
| 核对阶段实现与限制 | [delivery.yaml](../delivery.yaml)、对应验证报告 |
| 看本轮全流程实际结果 | [R19_FULL_FLOW_20261027.md](R19_FULL_FLOW_20261027.md)，七组/11次，待复核与失败分别记录 |
| 核对 R19 用例来源 | [catalog.yaml](../plans/r19/catalog.yaml)、cases/r19/、sources/r19/manifest.json |
| 检查实际提交内容 | voiceqa/gitflow.py、.githooks/、git diff --cached |

## 命令与实现

| 文件 | 职责 |
|---|---|
| `qa.py` | CLI 入口，不承载设备特定逻辑 |
| `voiceqa/cli.py` | validate/inspect/prepare/library-sync/library-prepare/run/watch/run-suite/verify/record/record-suite/import-analysis/doctor/serve/hook |
| `voiceqa/core.py` | 用例读取、bindings、schema、哈希与证据结构、Adapter 边界 |
| `voiceqa/runner.py` | 单用例编排、步骤、失败跳过、按会话状态 cleanup、收尾与证据封装；具体改动的实测情况见对应报告 |
| `voiceqa/interfaces.py` | TTS/AudioIO/DeviceEventSource 等可替换组件及参考实现 |
| `voiceqa/adapters.py` | Native/Replay/HTTP、ADB 日志、trace 与能力 |
| `voiceqa/audio_worker.py` | 子进程主线程驱动、文件播放、连续采集、电平和有界 IPC |
| `voiceqa/interaction.py` | wait_speech/wait_output：字幕与未归属说话者 mic 活动的调度代理 |
| `voiceqa/lifecycle.py` | 本次 started 的会话状态、终态/被替代检测，按 idle_countdown 计算完整退出 WAV 的主机预算 |
| `voiceqa/live.py` | 本机准备资产、实时进度、只读 watch |
| `voiceqa/speech_library.py` | 精确 profile 合成语音库、列表校验、补库、准备清单 |
| `voiceqa/batch.py` | suite 顺序/依赖、一次诊断复测、双证据包、继续政策与摘要 |
| `voiceqa/service.py` | HTTP 资产/会话/播放/等待/停止、认证与证据传输 |
| `voiceqa/analysis.py` | 分析 schema、原录音 SHA 绑定、追加分析与重新封装 |
| `voiceqa/gitflow.py` | 门禁、最小记录导出、hook、暂存及推送快照校验 |
| `config.example.yaml` | 配置模板；实际用忽略的 config.local.yaml |
| `requirements.txt` / `requirements-lock.txt` | 依赖声明与固定安装版本 |

## 用例、计划与 fixture

| 路径 | 用途与边界 |
|---|---|
| `cases/voice-smoke.yaml` | 基础流程 smoke，可 replay |
| `cases/host-audio-probe.yaml` | 主机播放/采集探针 |
| `cases/legacy-barge-unbound.yaml` | 旧打断场景缺绑定时的明确阻塞 |
| `cases/r19/R19-A.yaml` … `R19-H.yaml` | 原来源拆分，缺项和依赖以 YAML 为准 |
| `cases/r19-live/A.yaml`、`B.yaml`、`C_H.yaml`、`D.yaml`、`E.yaml`、`F.yaml`、`G.yaml` | 本轮动作执行版，七组覆盖 A–H，C_H 保留同会话上下文；需人工产品复核 |
| `cases/r19-A-first-turn-debug.yaml` | A 首轮输入探针 |
| `cases/r19-A-second-turn-debug.yaml` | A 第二轮“我喜欢皮卡丘。”与兼容就绪探针 |
| `cases/r19-C-debug.yaml` | C 搜索话术/观察探针，不能代替完整 C+H |
| `plans/r19/catalog.yaml` | 来源及有序目录，不是可执行 suite |
| `plans/r19/live-suite.yaml` | 可执行七组 A/B/C_H/D/E/F/G，覆盖 A–H；本机 bindings 的 prepared.A 等扁平键提供清单路径 |
| `plans/examples/failure-policy.yaml` | 首轮失败→一次复测→继续→依赖跳过的离线可执行示例 |
| `fixtures/workflow.json` | 基础 replay 合成输入 |
| `fixtures/failure-policy.json` | 失败政策 replay 输入，不是实机日志 |
| `schema/case.schema.json` | 用例原语与结构合同 |
| `schema/analysis.schema.json` | 声学/说话者/语义分析结果合同 |

## 语音库与来源

| 路径 | 内容 |
|---|---|
| `speech-library/list.yaml` | id、准确文本、profile、相对文件、SHA、时长、格式与来源 |
| `speech-library/clips/*.wav` | 可随私人仓库下载的校验合成测试输入，不是环境录音 |
| `sources/technical-design-20261027.md` | 原技术方案参考，不自动授权其中操作 |
| `sources/legacy/AI-CMD-001-WAKE.yaml` | 飞书旧唤醒附件原文 |
| `sources/legacy/AI-BARGE-001.yaml` | 飞书旧打断附件原文 |
| `sources/manifest.json` | 旧附件来源和字节指纹 |
| `sources/r19/manifest.json` | 用户最终选定回执，原文件/脱敏副本 SHA |
| `sources/r19/R19-source.redacted.md` | R19 脱敏副本，完整原文件保留在用户桌面 |

## 文档、Git 与验证

| 路径 | 内容 |
|---|---|
| `AGENTS.md` | 唯一 Agent 规则入口 |
| `README.md` | 安装、语音库、运行、监看、失败政策、Git、Windows |
| `docs/INTERFACES.md` / `docs/openapi.yaml` | 组件/HTTP、能力、时钟、错误、证据边界 |
| `docs/SPEECH_LIBRARY.md` | 精确匹配、来源、补库、跨平台复用与门禁 |
| `docs/PITFALLS.md` | 症状、观察/原因、修复和未证明结论 |
| `docs/FILE_INDEX.md` | 本索引 |
| `docs/VALIDATION_20261027.md` | 初期原型/本机/HTTP 验证，不能替代最新实机验收 |
| `docs/validation-summary.json` | 初期机器可读摘要 |
| `docs/R19_DEBUG_20261027.md` | R19 探针、修复、诊断批次的已观察事实 |
| `docs/R19_FULL_FLOW_20261027.md` | 最新七组/11次真机全流程、日志语义复核、onset修复和4秒收尾校准；产品未全量通过 |
| `delivery.yaml` | 各阶段 status、交付路径、限制 |
| `.githooks/pre-commit` | 暂存快照门禁与回归 |
| `.githooks/pre-push` | 推送 revision 校验 |
| `.githooks/post-commit` | 本机提交 SHA 登记 |
| `.github/workflows/verify.yaml` | Mac/Windows/Linux 离线 CI |
| `.gitignore` | 忽略真实证据、本机配置、依赖、缓存 |
| `.gitattributes` | 文本 LF 与来源字节保留 |

`tests/test_workflow.py`、`test_live.py`、`test_readiness_and_assets.py` 覆盖编排/资产/监看；`test_audio_ownership.py` 覆盖音频生命周期；`test_interaction.py` 覆盖 JSON 事件、声音 onset 锁定、提前结束、会话隔离、条件 cleanup 与完整音频预算；`test_http.py` 覆盖接口；`test_batch.py` 覆盖复测/依赖；`test_analysis.py` 覆盖分析；`test_speech_library.py` 覆盖语音库；`test_gitflow.py` 覆盖快照/摘要。证明层级以具体断言为准。

当前阶段（2026-10-27）测试集合共 69 项，运行 `python -m unittest discover -s tests -v`；门禁运行 `python qa.py gate`，CI 入口是 `.github/workflows/verify.yaml`。历史报告中的数量是当时快照，不覆盖历史数字。离线测试不是产品声学验收。Native 可选扩展与 SemanticReviewer 合同见 INTERFACES.md；持续 AI 语义服务尚未配置。

## 本机运行目录

```text
.local/
  prepared/<name>/manifest.json   # 本机绝对路径的执行资产清单
  hook-events.jsonl               # 已提交 SHA 登记
runs/<run_id>/
  cases.yaml                     # 当次准确用例快照
  run_manifest.json              # mode、来源/设备、batch context
  tts/manifest.json, *.wav        # 当次使用的测试输入
  audio/mic_raw.wav              # 连续环境原始录音
  logs/device_redacted.log       # 脱敏设备正文
  logs/audio_worker.log          # 音频进程诊断
  progress.jsonl                 # 实时步骤进度
  events.jsonl                   # 结构化事件
  playback.jsonl, timeline.jsonl # 实际播放与步骤时间；no_op 不写为已播放
  steps/*.json                   # 每节点参数、返回、错误/未执行原因
  transcript.json                # 默认未分析，不冒充实际听到
  results.json, receipt.md        # 执行/产品/证据分层结果
  evidence_index.md, integrity.json
batches/<batch_id>/
  suite.yaml, batch_manifest.json, progress.jsonl
  results.json, report.md, integrity.json
  review/<case>/
    01-initial/                  # 首次尝试完整独立证据
    02-diagnostic/               # 至多一次；未触发则不存在
    review_index.md              # 人工音频/日志/结果入口
records/<run_id>/summary.json    # 真机最小可提交摘要
records/<batch_id>/suite_summary.json
```

产物随 mode、步骤和采集结果变化，缺失/不完整证据在结果中标明。`runs/active_run.json` 供 watch 跟随，不是历史结果。replay 的 Git 记录可含 fixture 结构，产品结论不是实机通过。

首批全量本机入口为 `batches/20261027T164232-041b5832/`。其中 A/B 的旧 cleanup 完成并不代表主动退出，实际日志为 idle_timeout；该批次保留原始包，按复核说明解释，不能用后来的执行器修复倒改历史结果。

4秒校准本机证据为 `runs/20261027T170134-d19fb4cd/`，Git 最小摘要为 `records/20261027T170134-d19fb4cd/summary.json`。主机播完约2.456秒且预算标志为true，设备仍idle_timeout，未观察到退出词ASR，不能登记主动退出成功。跨机原始证据可用性与完整结论见最新全流程报告。

## R22 实机验证（2026-10-28）

- 简短结论和录音入口：`docs/R22_DEVICE_VALIDATION_20261028.md`。
- 用例/语音/接口索引与限制：`docs/R22_AUTOMATION.md`、`cases/r22/`、`plans/r22/`、`sources/r22/manifest.json`。
- 原始音频日志留本机 `runs/`、`batches/`；Git交付为 `records/20261028*/` 的最小摘要。

- R22按原文反馈字段的正式填写稿：`docs/P003_SEARCH_R22_TEST_FEEDBACK_20261028.md`（9项×10字段；实听/等待/截图缺项明确标注）。

## R23（2026-10-29上午）

- 面向测试方的原文8字段反馈：`docs/P003_SEARCH_R23_TEST_FEEDBACK_20261029.md`。
- 用例/套件/来源：`cases/r23/`、`plans/r23/`、`sources/r23/manifest.json`。
- 独立麦克风录音ASR：`docs/RECORDED_AUDIO_ASR.md`、`voiceqa/recording_asr.py`、`requirements-asr.txt`。
- 本轮20次运行、批次与ASR最小指纹：`records/20261029*/`；原音频、转写全文和模型均只存本机。
