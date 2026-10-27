# P003 语音自动化验证

在主项目 `qa-automation/` 子目录维护的独立私有 Git 仓库。YAML 控制动作、步骤顺序、事件与等待；Mac、Windows、其他 Agent 共用同一入口和 HTTP 合同。

当前能验证自动化流程和音频/日志采集；中文 ASR、可靠的设备说话者区分、搜索 task/正文事件与自动语义判定尚未完成。**流程执行完成不等于产品验收通过。** 每一步交付与限制见 [delivery.yaml](delivery.yaml)，实际验证见 [验证报告](docs/VALIDATION_20261027.md)。

## 安装

Python 3.11+、Git；真机执行需要 ADB 与可用麦克风/扬声器。

```bash
python -m venv .venv
# Mac/Linux
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python qa.py install-hooks
# Windows PowerShell（不依赖激活脚本）
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe qa.py install-hooks
```

以下用 `python` 表示上述虚拟环境解释器。Windows 需 Git for Windows 以运行 Git hooks；麦克风访问和 SAPI 中文音色需在该设备核实。首次 clone 后必须 install-hooks，Git 不自动安装仓库内 hook。

## 实时调试与预生成音频

```bash
# 窗口1：只读跟随最新运行；停止监看不会停止测试
python qa.py watch --device-log
# 窗口2：准备阶段只生成文件，不录音、不播放
python qa.py prepare cases/r19-A-second-turn-debug.yaml --output .local/prepared/r19-A2
# 执行阶段仅使用准备好的文件；缺失、指纹或文本不符直接阻塞，不回退合成
python qa.py run cases/r19-A-second-turn-debug.yaml --mode live --config config.local.yaml --prepared-assets .local/prepared/r19-A2/manifest.json --verbose
```

`progress.jsonl` 实时记录步骤、预期事件、命中/未命中原因、超时和采集收尾；`events.jsonl` 记录设备事件，`logs/device_redacted.log` 保留脱敏设备正文。`FILE` 表示加载已有文件，`TTS` 只发生在准备阶段，`MATCH` 只表示日志条件命中。准备产物属于本机文件，不提交音频到Git。

普通聊天调试显式允许 `alternative_events: [input_ready_fallback]`，并先用本轮 `started/source=hardware` 绑定trace。返回保留实际事件名；严格用例不写此字段时仍只接受主事件。兼容路径不自动计为正常P2通过。

R19 A–H来源、步骤与缺项见 `plans/r19/catalog.yaml`；完整用例的待接入条件保持阻塞。`R19-A-01-DEBUG`、`R19-A-02-DEBUG` 与 `R19-C-DEBUG` 是单独调试探针，不能拼成完整A–H验收。R19回执采用其原有十字段，产品结论留给实际证据判定。

## 最短闭环

```bash
python qa.py validate cases/voice-smoke.yaml
python qa.py run cases/voice-smoke.yaml --mode replay --fixture fixtures/workflow.json
python qa.py verify runs/<run_id>
python qa.py record runs/<run_id>
# 显式选择本轮 records 文件，避免暂存其他工作
# git add records/<run_id>
# git commit -m "Record voice QA attempt"
# git push
```

replay 使用合成事件和静音占位 WAV，不播放、不录音、不碰设备，产品结论固定 not_applicable。实际运行每次使用独立目录，不覆盖历史尝试。失败也应 record，不能只交最终成功一次。

## 真机与远程适配器

复制 `config.example.yaml` 为 `config.local.yaml`，在本机 `doctor` 查询音频设备，填写本机 ADB 路径与唯一 transport；cloud SN 独立核对。配置不入 Git。程序不会改设备固件、开关、音量或系统服务。

```bash
python qa.py doctor
python qa.py run cases/host-audio-probe.yaml --mode live --config config.local.yaml
python qa.py run cases/voice-smoke.yaml --mode live --config config.local.yaml
# 不同环境由 HTTP 对接
python qa.py serve --config config.local.yaml
python qa.py run cases/voice-smoke.yaml --mode http --adapter-url http://127.0.0.1:8765
```

普通问候探针不使用搜索额度；正式搜索/打断/续讲用例必须绑定最新话术、有效窗口与任务事件。当前不自动补出这些业务条件。详细端点、返回值、时钟和能力边界见 [接口文档](docs/INTERFACES.md) 与 [OpenAPI](docs/openapi.yaml)。

## 旧 YAML 兼容

保留飞书附件中的 case_id/title/domain/severity/requirement_refs/tags/requires/blockers/preconditions/steps/cleanup/artifacts 及 kind/name/params。原始附件在 `sources/legacy/`，SHA256 与来源在 `sources/manifest.json`，飞书未改写。

```bash
python qa.py inspect sources/legacy/AI-CMD-001-WAKE.yaml
```

`{{asset...}}` 与 `{{contract...}}` 从本机 bindings 解析，未绑定时阻塞并保留逐步未执行记录。不会执行来自 YAML 或附件中的 shell。支持 audio_play/audio_record/wait_event/sleep/expect；vision_expect 与未知/未定义的语义合同需要另接分析器。原文件里的“已实现”标签不是本次运行证据。

## 交付目录与 Git hooks

- `cases/`：共用 YAML；`schema/`：用例与分析结果合同。
- `sources/`：原始方案与两个旧版样例、来源指纹。
- `voiceqa/`：执行器、可替换组件、HTTP 客户端与服务。
- `runs/<run_id>/`：本机原始音频、脱敏日志、逐步 JSON、回执与哈希，Git 忽略。
- `records/<run_id>/`：Git 可存的回放记录或真机最小摘要；真机原始证据留本地。
- `delivery.yaml`：每阶段实现状态、交付路径与限制。
- `pre-commit`：对**暂存快照**检查 YAML、交付文件、证据指纹并运行回归；工作区未暂存修复不能掩盖坏提交。
- `pre-push`：检查本次推送各本地 revision 的实际快照。
- `post-commit`：将提交 SHA 登记到本机 `.local/hook-events.jsonl`，不在 hook 内递归 commit/push。
- `.github/workflows/verify.yaml`：Mac/Windows/Linux 的相同离线门禁。

hook 检查“交付是否可核查”，不会因存在文档就把 partial/blocked 改为已完成。hooks 可以被 Git 用户绕过，远端 CI 是补充证据；尚未配置分支保护规则。

## 在 Windows 接入同一仓库

在 Windows 的主项目目录下 clone 本仓库为 `qa-automation`；将 `/qa-automation/` 加入**父仓库本机** `.git/info/exclude`，避免误提交嵌套仓库。这里使用独立仓库，不是 submodule。两边只同步源码/YAML/最小摘要，本机配置和原始证据各自保存。不要把 Mac 的绝对路径、音频设备编号或 ADB transport 照搬到 Windows。

维护时运行 `python -m unittest discover -s tests -v` 和 `python qa.py gate`。`record` 与 Git commit/push 是显式操作，不会自动向飞书或同事发送信息。

## 失败后独立采证复测一次，再继续

```bash
# 离线验证“首轮失败→复测一次→继续独立用例→跳过失效依赖”
python qa.py run-suite plans/examples/failure-policy.yaml --mode replay --fixture fixtures/failure-policy.json
# 实机suite每项指定prepared_assets；完整资产和话术均复用
python qa.py run-suite <suite.yaml> --mode live --config config.local.yaml --verbose
python qa.py record-suite batches/<batch_id>
```

首轮未通过会锁定结果，诊断复测不覆盖它。两次的完整录音、日志、步骤结果分别存到 `batches/<batch_id>/review/<case>/`，人工从review_index.md复核。依赖失败用例的场景跳过，其他独立用例继续。配置/能力阻塞与产品未通过分别记录，缺少判定器保持待复核。Ctrl+C停止执行队列后不会再复测。
