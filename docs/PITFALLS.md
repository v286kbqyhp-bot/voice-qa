# 踩坑与排查

本页记录已观察问题、处理方式和证据边界。历史运行在本机 `runs/` / `batches/`；Git 只保存摘要，原始录音不会随 clone 下载。最新事实见 [全流程报告](R19_FULL_FLOW_20261027.md)，历史探针见 [R19 调试](R19_DEBUG_20261027.md)，合同见 [INTERFACES.md](INTERFACES.md)。

## 声音与收尾

| 现象 | 观察与处理 |
|---|---|
| PortAudio 返回成功，用户没听到 | 调用成功不证明物理起音，当时根因未定。Mac 默认改用 `afplay` 播放预生成文件，用户后来确认自动重播可听到；路由、摆位和目标声音仍需验证 |
| HTTP 音频 stop 挂住 | 驱动在线程间启动/停止曾不能可靠收尾。现由 `ProcessAudio` 放在子进程主线程，通过有界 JSON IPC 控制；超时回收并保留不完整证据 |
| WAV 非零，就当设备回答已采到 | 非零可能来自测试输入或环境。`valid_capture` 不证明指定设备说了指定内容，需复听或导入绑定原音频 SHA 的分析 |
| 用字幕填写“实际听到” | 设备字幕、设备 ASR 与声学证据分别记录；未分析音频保持待复核，caption 不证明扬声器输出 |
| “不聊了”已播放就当退出成功 | 播放完成不证明 ASR 识别，ready_next_wake 也可能源于 idle_timeout。先看当前会话状态及实际关闭原因，主动退出专项再核对话术之后的 user_session_exit |
| 临时合成错过节点 | 先 `library-prepare`，运行传 `--prepared-assets`，节点仅播放文件。缺失或指纹不符时阻塞，不静默实时合成 |

入口：`voiceqa/interfaces.py`、`audio_worker.py`、`adapters.py`；回归：`tests/test_audio_ownership.py`、`test_http.py`、`test_readiness_and_assets.py`。

## 事件超时与误触发

| 现象 | 观察与处理 |
|---|---|
| A2 超时，没说“我喜欢皮卡丘。” | `20261027T153812-cbcc9b83` 设备给 fallback，旧脚本仅等 input_ready，句子未播放。显式列 `alternative_events: [input_ready_fallback]` 并限当前 trace 后，`20261027T154321-62b9f18c` 命中并播放；fallback 保留原名，不能冒充正常就绪 |
| 启动即命中旧日志或其他会话 | 用本轮 `started/source=hardware` 绑定 trace，后续 `same_trace=true`，保留接收单调时间与原日志。延迟日志仍需核对，不宣称已取得可靠 task 映射 |
| speaking=false 后插话撞上正文 | 它可能是垫话结束或短停顿，C 曾在 false 后再次 true。不能单独当整轮结束；最终字幕、后续状态、mic 静音与远端计数的组合仍只是调度代理 |
| 有字幕但打断窗口已过 | 字幕与声学起音不同步。`wait_speech` 记录字幕+mic 活动时间代理，过窗返回 timing_missed；实际重叠与说话者归属需复听 |
| D 一直等不到 delay_s，最终超时 | 旧代码每轮从短 rolling levels history 重算 onset，历史窗口滑动导致起点不断后移。现在锁定每段首次声音活动时间，再累计 elapsed；修复不追溯改变首批失败结果，实机复测另记 |
| 客户端已结束却继续等到通用超时 | wait_speech/wait_output 现检查本轮 lifecycle，ending/closed/superseded 立即返回 session_ended_while_waiting 并保留 idle_timeout 等实际原因 |
| runner 超时就判性能不达标 | 测试超时是执行边界，不自动等于产品 SLA。区分执行超时、条件/能力阻塞与产品判断 |
| 固定 sleep 偶尔能跑通 | 到时不代表状态成立。优先明确事件或带证据层级的调度代理，保留 timeout 和实际返回 |
| Native 可跑，HTTP 跑新等待却阻塞 | wait_speech/wait_output、session_status、play_cleanup 目前仅 Native 提供；HTTP 仍是基础原语合同。先查能力，不因同用 YAML 就假设远端支持新等待与收尾 |

入口：`voiceqa/adapters.py`、`interaction.py`、`runner.py`。新接入 `wait_speech` / `wait_output` 不能写成可靠搜索 task、正文或整轮完成合同。

## ADB、版本与时钟

| 现象 | 处理 |
|---|---|
| PATH 没有 adb | 先查配置和 `~/Library/Android/sdk/platform-tools/adb`；不能只凭 `command -v adb` 断言未安装 |
| Android date 格式报错 | 远端 shell 需保留格式串引号；参考实现用 `date '+%m-%d %H:%M:%S'`，本机 argv 分隔不等于远端引号仍在 |
| dumpsys 多个 versionName | 结合 `pm path` 核对。当前注明 adb_dumpsys_active_first，不随意取最后一个，也不宣称覆盖所有 ROM 的版本解析 |
| 将 ADB 序列号当 cloud SN | 身份独立核对，摘要只写已确认后缀 |
| 跨 Mac/Windows 相减单调时钟 | 只在同一时钟域比较；使用服务端同域观测或另做同步 |

## 语音库与路径

| 现象 | 处理 |
|---|---|
| C 播放前找不到 WAV | `20261027T155316-8d4b9fbe` 相对路径被再次拼到 cases/。本机准备清单用绝对路径；版本化库列表保持库内相对路径，目标机重新 library-prepare |
| Windows 清单指向 Mac 用户目录 | .local/prepared/ 是本机产物，不同步；WAV 库可同步，执行清单在目标机重建 |
| 标点变化却复用旧句 | 准确文本属于 profile，必须新条目，不模糊匹配测试话术 |
| 库指纹不符却自动覆盖 | 校验失败阻塞，恢复受信文件，不静默覆盖损坏或篡改资产 |
| 换引擎沿用同名 rate | provider/voice/rate/locale 共同区分；Mac 175 与 Windows 默认 rate 不能当等值 |
| 导入 mic 或 replay WAV | 仅可验证 generated_tts 入库；真实录音、未知外部 WAV 和回放占位文件不是合成输入 |

命令和字段见 [SPEECH_LIBRARY.md](SPEECH_LIBRARY.md)，实现/回归见 `voiceqa/speech_library.py`、`tests/test_speech_library.py`。

## 复测与结果

- 首次未通过不能被第二次改善覆盖。历史 C 诊断批次 `20261027T160656-886bbcb2` 保留两包，最终 `not_passed`；诊断字幕改善仍需复听。
- 复测须独立采证，不重用旧录音，不将两次片段拼成一次结果；人工从 `review_index.md` 开始。
- 缺配置、素材或未实现能力记 `blocked`，不靠盲重试补前置；执行完成但没有 oracle 为 `pending_review`。
- 中途失败若直接进入下一例，旧会话会污染下一次唤醒。收尾先检查当前会话：已关闭就记录实际原因直接收尾，仍活跃才按需播放预生成“不聊了”并等关闭；保留首个失败原因，用户 Ctrl+C 后不再发起清理语音。
- 不能将客户端自行关闭当主动退出。首批全量批次 `20261027T164232-041b5832` 中，A (`20261027T164232-d4e0cb78`) 与 B (`20261027T164308-b0d4acd0`) 的 session_end_requested 明确 reason=idle_timeout，旧 cleanup 只等 ready_next_wake，曾记录完成；C_H (`20261027T164409-9a4a6f0e`) 则有本轮 user_session_exit。历史包保留原结果，新增复核说明，不能修改成主动退出成功。
- 普通收尾不需要每次强制说“不聊了”，已 idle_timeout 的会话再次播放没有意义。G 主动退出专项仍需在本轮、退出话术之后观察 user_session_exit；空闲关闭不能替代这个断言。
- cleanup 的 audio_play 与 wait_event 用 `if_session_active:true`，仅关联本次 started；未知/未绑定则阻塞，不能猜会话状态。终态跳过的动作在 steps 中保留 no_op 与实际原因。
- 活跃会话也不能无视剩余时间强播。play_cleanup 读取同 trace 的 voice.idle_countdown，按 `idle_finish_before_s:4.0` 要求在倒计时开始的前 4 秒内完成，估算包含完整 WAV 时长和 `startup_budget_s:1.0`。预算不足则 no_op/cleanup_deadline_missed 并等自然结束，不能将未播放写进 playback.jsonl；无可用倒计时明确阻塞。
- 倒计时预算来自设备日志的主机接收时间，是调度代理。host_playback_finished_in_budget 不证明物理扬声器起音或“不聊了”已被识别。
- 实测 `20261027T170134-d19fb4cd` 主机在推算空闲起点后约2.456秒播完，预算标志true，设备仍idle_timeout且无退出词final ASR。不要因调度达标而把退出识别写成功。
- H 的 final ASR 缺少后半句，回答出现上下文语义异常；缺少完整输入和声学复核时不能武断归因，但该项不能记通过。持续AI服务尚未配置，字面ack词表和本轮Agent日志复核不能冒充完整实时语义系统。
- H 依赖 C 的同会话上下文；前置未通过/上下文缺失时跳过依赖，继续独立场景，不能伪造上下文。
- replay 只证明流程；不能证明设备回答、声学打断或 Windows 真机链路。Ctrl+C 停止执行后不继续队列，停止 watch 只结束只读查看。

入口/回归：`voiceqa/batch.py`、`tests/test_batch.py`、`plans/examples/failure-policy.yaml`。

## Windows、Git 与来源

| 现象 | 处理 |
|---|---|
| Windows 中文输出或 IPC 编码异常 | CLI 显式 UTF-8，音频 worker 私有 JSON 行用 ASCII 转义，避免管道默认编码差异；文本 LF，来源字节另行保留 |
| clone 后 hook 未运行 | 每台机 `python qa.py install-hooks`，Windows 需 Git for Windows |
| 工作区修好但暂存坏文件 | pre-commit 校验暂存快照，修正后重新暂存；pre-push 校验实际 revision，不能用工作区替代 |
| 离线测试通过就说真机全通过 | 分别核对实机报告、执行状态、产品判断与原始录音 |
| 原始录音/大日志进入 Git | runs/batches 留本机，record/record-suite 导出最小摘要；只有库内校验通过的合成输入属于许可音频 |
| 父项目误暂存嵌套仓库 | 父仓库本机 .git/info/exclude 加 /qa-automation/，不要意外变成 gitlink/submodule |
| 用错 R19 回执 | 用户最终选桌面 P003_SEARCH_R19_DEVICE_TEST_RECEIPT_20261027.md，按 sources/r19/manifest.json 的 SHA 与脱敏副本核对 |
| prepared.A 明明填了仍 unresolved binding | bindings 使用完整的扁平键 prepared.A，不是 prepared 下嵌 A；参见 README 的 live-suite 示例 |

结论保留“已观察”“待确认”“未验证”的边界，不根据历史标签、字幕或单条 probe 扩大结论。
