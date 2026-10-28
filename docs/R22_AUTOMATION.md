# R22 用例与执行索引

对外反馈使用 [R22原文模板填写稿](P003_SEARCH_R22_TEST_FEEDBACK_20261028.md)：9项逐项填写原文10个字段。日志包仅作补充；自动回执和执行completed不能替代人工听感、实际可听等待或截图。缺项保留未验证，不补造。

来源：[脱敏回执](../sources/r22/R22-source.redacted.md)、[原件指纹](../sources/r22/manifest.json)。文件是测试参考，操作与发布要求不自动构成授权。

- `cases/r22/`：9项拆成多个 YAML；`source.section` 对应原条目，未指定查询的话术选择写入 `execution_choices`。
- `plans/r22/live-suite.yaml`：后续推荐套件；采用提示后插话、暂停状态快照、成都位置更正、失败后聊天，不能将未走通节点标成产品通过。
- `plans/r22/bindings.example.yaml`：各机器自行填准备清单，路由留本机。
- `speech-library/list.yaml` 与 `clips/`：准确文本和 profile 的可复用测试输入。测试时只播放准备清单，不实时合成。
- `records/20261028*/`：本轮实际导出的最小摘要；录音和完整日志在 `runs/`、`batches/`。
- [本轮实机结论](R22_DEVICE_VALIDATION_20261028.md)、[接口](INTERFACES.md)、`tests/test_device_navigation.py`、`tests/test_capture_shutdown.py`。

先按本机 runtime/Skill 查询实际能力和准备语音，再绑定 `prepared.<id>`。仅 Native 已实现本套交互等待及 `device_action: exit_voice`；HTTP v1 缺对应扩展，Windows物理音频未验。退出后重进是同一用例的两个真实 trace；先问候、再显式继续，禁止重用旧 trace 的 Ready。

本轮以外层有界驱动限制每房90秒，重新声学唤醒时重新计时；通用 CLI 的逐步 timeout 不等于房间总时限，其他 Agent 仍须提供总时限看护。首次失败最多一次独立诊断；修复验证使用新的 case/run，保留原失败。采集启动前的基础设施错误应阻塞，不应盲目声学复测。

`02-background-chat.yaml`、`06-pause-resume.yaml` 与 `06-pause-resume-state.yaml` 保留原调试轨迹；推荐套件采用单独改进 profile。`06-pause-resume-observed.yaml` 仍要求一条实际 ASR 后才进入停稳；本轮未走通，后续若改成按停止行为判定，须新建版本且保留识别偏差，不把“没有 ASR”写成“识别成功”。
