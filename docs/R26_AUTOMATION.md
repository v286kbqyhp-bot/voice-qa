# R26 用例、续测与证据

来源为 `sources/r26/manifest.json`，原文10组、反馈6字段。现场367/尾号00100004；R26云端发布状态仅据提供回执。完整SN只在本机转发稿，仓库保留后8位。

本轮先完成6次尝试后异常停止，首次反馈保留 `docs/P003_SEARCH_R26_TEST_FEEDBACK_20261030.md`。用户明确要求继续，随后又纠正硬件项应由执行器发声、用户观察，并要求升高暂不测试、额外复测音量。最新合并反馈为 `docs/P003_SEARCH_R26_CONTINUED_TEST_FEEDBACK_20261030.md`：累计23次独立运行和独立录音ASR，不覆盖初次失败。

目录 `plans/r26/catalog.yaml` 不是自动suite；现有run-suite固定继续独立用例，不实现本轮逐项Agent检查/用户观察策略。用 `qa.py validate <case>`、`library-prepare <case> --output <new-local-dir>` 和 live+本机配置+prepared-assets 单条执行。CLI/HTTP能力边界沿用INTERFACES，不把Mac配置或prepared清单同步为Windows本机路径。

第4组等待中改查实际只识别末尾，天气返回无可靠信息。第5组北京失败，原“成功结果播完后改查”前置未满足；`05-city-after-failure.yaml` 是单次诊断，连接失败未播成都，不能当已测改查。第6组有运球→学校→明确继续运球的真实录音，从未完成句句首重说符合本轮允许表现；无task_id不证明内部身份。第7组真实退出后的新房失败，静默/继续核心步骤未执行。

`08-explicit-exit.yaml` 的session_end_requested无trace，所以按绑定房、退出输入之后的时间窗和明确reason核对；不能要求日志未提供的trace。首次只识别“出聊天”、未语音退出。`08-exit-after-ack.yaml` 是等待确认词完成的安静对照，因未入房而未执行，不能判安静场景失败。

硬件第9项由用户观察实体变化。原完整 `09-device-actions.yaml` 含升高，用户暂缓后已加blockers，禁止本轮执行。`09a/09b/09c`初稿因等待云端input_ready而未发词，是执行流程限制；用户纠正后`*-direct.yaml`只等真实硬件wake绑定、留本地招呼裕量，实际播放三句，区别于云端入房验收。仅对音量按用户要求额外复测一次：`09c-volume-retest.yaml`留4秒裕量，不将它充当input_ready证据。用户确认“小了”，端侧ASR、label38、audio.volume_changed65和media/assistant 70→65闭环；最后reason=device_command_completed。未获逐项实体灯光变化结论，录音亮度最低提示不等于实体变暗。不执行升降，也不用ADB直接控制硬件代替语音。

连接错误有两种原始证据：6次connect_failed/TimeoutException、6次未被当前解析器结构化的`failure stage=wake_connect type=NetworkTimeout`。复核应查脱敏原日志，不能只统计events中的connect_failed。系统VALIDATED不是RTC成功证明；应用PID/六服务稳定也不代表语音正常。

23次录音有效标记、音频SHA和独立ASR分析SHA核对；ASR小模型错字/未分离说话人/时间估计限制保留。真实录音、全文转写及截图留本机，Git只入`records/<id>`最小摘要。音量末次实体通过不洗掉上次无响应；第9项部分通过、第5/7/10核心步骤仍缺，不报全量验收通过。Mac音量未改，设备音量维持用户已确认的65。
