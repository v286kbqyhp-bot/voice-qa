# P003 语音自动化验证

本目录是父项目内的独立私人 Git 仓库。YAML 控制用例、步骤顺序、播放与等待；TTS、音频、设备日志、分析器通过接口替换，Mac、Windows 和其他 Agent 共用命令入口与 HTTP 合同。

框架保存每一步动作与证据，执行完成不能自动等同产品验收通过。设备字幕、ASR、麦克风活动与实际听到的回答分层记录；中文声学转写、可靠说话者归属和产品语义判定尚需分析器或人工复核。

- Agent 规则：[AGENTS.md](AGENTS.md)
- 设备/环境对接：[接口文档](docs/INTERFACES.md)、[OpenAPI](docs/openapi.yaml)
- 操作遇到问题：[踩坑与排查](docs/PITFALLS.md)
- 找入口和证据：[文件索引](docs/FILE_INDEX.md)
- 实现交付状态：[delivery.yaml](delivery.yaml)
- 最新实测：[R19 全流程与收尾校准](docs/R19_FULL_FLOW_20261027.md)；历史记录：[原型验证](docs/VALIDATION_20261027.md)、[R19 调试](docs/R19_DEBUG_20261027.md)

2026-10-27 首批实际遍历 A–H 七组、11 次独立采集：A/B/C_H 动作完成待复核，D/E/F/G 各尝试两次后仍未完成关键等待。H 有日志语义异常，且输入后半句未出现在 final ASR，尚未验收。该结果验证了失败采证、复测和继续流程，不能记产品全量通过，详情与证据入口见最新报告。

## 安装与本机准备

需要 Python 3.11+、Git；真机执行另需 ADB、麦克风和扬声器。仓库地址为 `git@github.com:v286kbqyhp-bot/p003-voice-qa.git`。

```bash
python -m venv .venv
# Mac/Linux
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python qa.py install-hooks
# Windows PowerShell，无需激活虚拟环境
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe qa.py install-hooks
```

下文 `python` 指对应虚拟环境解释器。每台电脑 clone 后都需安装 hooks；Windows 需 Git for Windows，麦克风权限与实际输出需在该机验证。复制 `config.example.yaml` 为 `config.local.yaml`，运行 `python qa.py doctor` 查询本机音频设备并填写本机 ADB 配置。配置文件不入 Git。

Mac 默认用 `afplay` 播放已生成文件，Windows 参考实现用 PortAudio；合成器可以替换。命令找不到 adb 不等于没有安装，当前实现还检查 Mac SDK 默认路径，也支持配置绝对路径。ADB transport 与 cloud SN 分开核对。

## 语音库：已有直接复用，缺失再生成

[`speech-library/list.yaml`](speech-library/list.yaml) 是准确文本与合成配置到 PCM16 WAV 的列表，文件位于 `speech-library/clips/`。clone/pull 后其他设备可直接校验并播放，无需重合成。已收录 R19 已定义话术和常用唤醒/退出句；字段、提供方与补库见 [语音库说明](docs/SPEECH_LIBRARY.md)。

```bash
# 准备阶段，不播放、不录音，相同文本和配置直接复用
python qa.py library-sync cases/r19
# 为单用例生成本机执行清单，输出目录必须是新目录
python qa.py library-prepare cases/r19-A-second-turn-debug.yaml --output .local/prepared/r19-A2
# 执行只播放清单内文件，校验失败阻塞，不临时合成
python qa.py run cases/r19-A-second-turn-debug.yaml --mode live --config config.local.yaml --prepared-assets .local/prepared/r19-A2/manifest.json --verbose
```

库按 text、voice、rate、locale、provider 精确匹配，标点变化也属于不同输入。已有条目损坏时不静默重生成。默认库 profile 是 `macos_say / Tingting / 175 / zh-CN`；Windows 可直接使用这些 WAV，缺失输入可由 Mac 或 HTTP 合成服务补库。不同引擎的同名 rate 不代表相同语速。

`prepare` 是独立的本机合成入口；日常优先使用 `library-prepare` 共享资产。库中经列表与指纹校验的 generated_tts 音频可入 Git，真实麦克风录音、设备回答与原始日志留本机。

## 边执行边看日志

```bash
# 单独终端只读跟随最新运行
python qa.py watch --device-log
# 或固定运行
python qa.py watch --run <run_id> --device-log
```

`progress.jsonl` 显示步骤、预期条件、命中/拒绝原因、超时与收尾；`events.jsonl` 保存结构化事件，`logs/device_redacted.log` 保存脱敏设备正文。FILE 是加载已有文件，MATCH 是条件命中，均不能单独证明产品通过。`--verbose` 将进度发到 stderr，最终 JSON 保留在 stdout。

停止 watch 不停止测试；停止执行器会终止队列。已执行/未执行步骤和采证错误仍保留在本轮目录中。

## 一个节点何时进入下一个节点

`audio_play` 在本机文件播放调用完成后返回，下一步再按 YAML 等事件、观察或播放。`wait_event` 匹配明确事件、字段与当前 trace，例如先绑定本轮 `started/source=hardware`，再等 `same_trace` 的 input_ready。仅在用例显式列出时接受 input_ready_fallback，返回保留实际名字。

新增 `wait_speech` / `wait_output` 将设备字幕、说话状态、远端音频计数与连续麦克风电平组合成有界调度代理，用于找开口时机或稳定静音间隙。它们不能证明正式整轮结束、说话者身份、搜索正文语义或实际物理重叠，实际打断仍需复听。worker_speaking=false 本身可能只是垫话后的间隙。协议和能力见 [接口文档](docs/INTERFACES.md)，本轮实机结论以验证报告为准。

打断延迟按每段首次观察到的声音活动时间锁定，不能因短 rolling history 滑动而重新起算；首批 D 使用的旧逻辑存在持续重置 onset、无法达到 delay_s 的执行器问题，已修正并通过离线回归。D 修复后真机复测只有过渡话术、没有正文，仍不能证明完整打断/续讲通过。等待中若会话提前结束，会立即返回 session_ended_while_waiting 与实际原因，不再白等通用 timeout。

主流程中途失败会跳过后续主步骤并保留首个失败原因。会话收尾按状态处理：先查当前会话是否已关闭，已关闭直接记录实际原因；仍活跃才按需播放预生成退出语音并等关闭，避免污染下一例。用户 Ctrl+C 后不再发起清理语音。

客户端 idle_timeout 只表示自行关闭，不能写成执行器主动退出；播放完“不聊了”也不能证明设备识别到了它。普通收尾不要求每次强制说“不聊了”。G 等主动退出专项需本轮、退出话术之后的 user_session_exit 证据。首批全量批次 `20261027T164232-041b5832` 的 A/B 曾仅凭 ready_next_wake 完成 cleanup，日志实为 idle_timeout；这些历史结果保留，复核说明单独补充，不能回写成主动退出成功。

`cases/r19-live/` 的收尾参数如下，当前由 NativeAdapter 提供：

| 参数 | 含义 |
|---|---|
| `if_session_active: true` | 按本次绑定的 started 判断；ending/closed/superseded 不再发清理语音，closed/superseded 不再等旧关闭事件；未绑定则阻塞 |
| `idle_finish_before_s: 4.0` | 从设备本次 idle_countdown 开始起算，要求完整 WAV 在 4 秒窗口内完成；不是收到日志后再给 4 秒 |
| `startup_budget_s: 1.0` | 估算总耗时为 WAV duration + 1 秒启动余量，必须放得下当前可用窗口 |

预算使用同 trace 的 voice.idle_countdown(timeout_ms/remaining_ms/running) 和主机接收时间；若放不下，返回 no_op、playback_started=false、cleanup_deadline_missed，等自然关闭，不把未播放动作写入 playback.jsonl。缺少可用倒计时则明确阻塞。实际主机播放结束是否在窗内另记 host_playback_finished_in_budget；该预算不能保证物理起音、网络识别或客户端接收时刻。

A 校准运行 `20261027T170134-d19fb4cd` 在主机日志代理的空闲起点后约 2.456 秒播完，host_playback_finished_in_budget=true；设备仍 idle_timeout，未观察到退出词 final ASR 或 user_session_exit。这里只证明播放调度在预算内，退出识别尚未闭环，见 [实测报告](docs/R19_FULL_FLOW_20261027.md)。

## 失败复测一次，继续独立用例

suite 明确顺序与依赖，策略固定为首次失败后独立采证复测一次、继续后续独立用例。首次 not_passed 保持未通过，诊断改善不能覆盖初轮。配置/能力/素材阻塞分别记录，不盲目复测；依赖失败上下文的用例记 skipped_dependency。

```bash
# 无硬件验证失败政策
python qa.py run-suite plans/examples/failure-policy.yaml --mode replay --fixture fixtures/failure-policy.json
# 真机 suite 每项指定 prepared_assets
python qa.py run-suite <suite.yaml> --mode live --config config.local.yaml --verbose
python qa.py record-suite batches/<batch_id>
```

每次尝试独立录音、抓日志、保存步骤并生成 SHA256。人工从 `batches/<batch_id>/review/<case>/review_index.md` 查看 01-initial/ 和 02-diagnostic/，分别打开音频、日志和结果；采证失败明确写未取得。执行完成但没有判定器是待复核，不自动 PASS。manifest context 保存批次、尝试角色和首次运行 ID。

[plans/r19/catalog.yaml](plans/r19/catalog.yaml) 是 A–H 来源目录，不能直接当可执行 suite。`cases/r19/` 保留原来源拆分与待绑定条件，三个 *-debug.yaml 是单独探针。`cases/r19-live/` 是本轮动作执行版：A、B、C_H、D、E、F、G 七组覆盖 A–H，C 与 H 合并以保留同会话上下文；执行完成仍需产品复核，不能用探针拼成完整验收。

可执行入口为 [plans/r19/live-suite.yaml](plans/r19/live-suite.yaml)。先为七组分别运行 library-prepare，再在本机 config.local.yaml 填写以下**扁平键**；`prepared.A` 是完整键名，不是嵌套对象。路径用本机生成的清单绝对路径，Windows 同样需本机重建。

```yaml
bindings:
  prepared.A: /absolute/path/A/manifest.json
  prepared.B: /absolute/path/B/manifest.json
  prepared.C_H: /absolute/path/C_H/manifest.json
  prepared.D: /absolute/path/D/manifest.json
  prepared.E: /absolute/path/E/manifest.json
  prepared.F: /absolute/path/F/manifest.json
  prepared.G: /absolute/path/G/manifest.json
```

```bash
python qa.py run-suite plans/r19/live-suite.yaml --mode live --config config.local.yaml --verbose
```

## 离线闭环与 Git 交付

```bash
python qa.py validate cases/voice-smoke.yaml
python qa.py run cases/voice-smoke.yaml --mode replay --fixture fixtures/workflow.json
python qa.py verify runs/<run_id>
python qa.py record runs/<run_id>
# 明确选择本轮摘要和工程文件后提交
# git add records/<run_id>
# git commit -m "Record voice QA attempt"
# git push
```

replay 不播放、不录音、不操作设备，产品结论为 not_applicable。失败也需保留记录，不只交最后成功一次。真实执行证据在 runs/，批次证据在 batches/，均被 Git 忽略；record/record-suite 仅导出适合入 Git 的最小摘要。

| 入口 | 检查对象与产物 |
|---|---|
| pre-commit | 暂存快照的 YAML、交付文件、证据和许可语音库，运行回归；未暂存修复不能掩盖坏提交 |
| pre-push | 本次推送的实际本地 revision 快照 |
| post-commit | 向本机 .local/hook-events.jsonl 登记提交 SHA，不递归提交/推送 |
| GitHub Actions | Mac/Windows/Linux 离线测试、门禁与 replay smoke |

当前阶段（2026-10-27）的 [离线测试集合](tests/) 有 68 项；维护运行 `python -m unittest discover -s tests -v` 与 `python qa.py gate`，交付检查入口为 [.github/workflows/verify.yaml](.github/workflows/verify.yaml) 和 hooks。历史报告保留各自当时的测试数。hooks 可被用户绕过，远端 CI 是补充证据；尚未配置分支保护。离线 CI 通过不能代表 Windows 真机音频验收通过。

## 其他设备和 Agent 接口

```bash
# 在连接设备的电脑启动服务
python qa.py serve --config config.local.yaml --host 127.0.0.1 --port 8765
# 客户端沿用相同 YAML 和准备好的 WAV
python qa.py run <case.yaml> --mode http --adapter-url http://127.0.0.1:8765 --prepared-assets <manifest.json>
```

服务负责设备路由，调用方负责 YAML、资产和证据。默认只监听本机；局域网用环境变量 VQA_API_TOKEN，不写入 YAML/Git。接口不接收远程 shell 或任意主机路径。一个服务只允许一个采集会话，当前租约 180 秒、单次等待/观察最多 60 秒，尚无续租。跨电脑单调时钟不能直接相减。

现有 HTTP 合同提供资产、播放、wait-event、observe 与 stop。新增 wait_speech/wait_output、session_status 和 play_cleanup 当前由 NativeAdapter 提供；HTTP 客户端尚未提供同等联合等待与条件收尾能力，r19-live 用例应使用 live 模式，远端接入需先补齐相应合同。

组件、能力、JSON 返回、错误、分析导入与时钟边界见 [INTERFACES.md](docs/INTERFACES.md)。其中 SemanticReviewer.review(context, events) 已定义语义评审合同，后台持续 AI 服务尚未配置；本轮 Agent 读取日志复核不能当作实时模型系统已部署。未实现的能力须显式声明，不编造设备识别结果。

## Windows 接入

在 Windows 父项目下 clone 本仓库为 qa-automation，并将 `/qa-automation/` 加入**父仓库本机** .git/info/exclude，避免误提交嵌套仓库。本目录是独立仓库，非 submodule。两端同步源码、YAML、许可合成语音和最小摘要，本机配置与原始录音各自保存。

使用 UTF-8 与仓库 LF 行尾。Mac 绝对路径、音频编号、ADB transport 不可照搬；执行清单在目标机重新 library-prepare，会复用已下载的库内 WAV。Windows SAPI、系统音频和麦克风权限仍需 Windows 实机验收。

## 来源与旧 YAML

旧飞书附件在 sources/legacy/，指纹在 sources/manifest.json。保留 case_id/title/domain/severity/requirement_refs/tags/requires/blockers/preconditions/steps/cleanup/artifacts 及 kind/name/params，未绑定条件记录阻塞。`python qa.py inspect sources/legacy/AI-CMD-001-WAKE.yaml` 可只读查看支持情况。

用户最终选定 R19 来源的脱敏副本和指纹在 sources/r19/，来源中的“已实现”或预期不是本次证据。每轮状态从对应运行/批次回执核对，定位见 [FILE_INDEX.md](docs/FILE_INDEX.md)。
