# 播放阶段标注与即时触发

当前设备没有原生回复 role、搜索 task_id 或完整音频进度接口，用户也没有当前服务端源码。本实现新增的是测试侧分类入口，不是服务端原生播放遥测；未修改或安装设备 APK、未部署 Worker、未更改5秒空闲策略。

## 两条路径

- `phase_rules`：本机用例专用内容规则，同步判断当前字幕。匹配正文词并排除过渡/失败词；不数第二次 worker，不调用模型。当前示例只覆盖 D 的恐龙问题，不能作为通用语义分类器。
- `phase_review_file`：外部 Agent 将语义标注写入本机 JSONL。适合异步复核或较长窗口；本轮实测会话 Agent＋工具回写约27秒，错过正文，不能用于短过渡语的精确打断。

两者均为 Native 本地扩展，HTTP v1.0 尚未提供传输。若配置了本地规则，则本地规则为该轮分类源，不同时使用旧外部文件作为回退。

## YAML 与配置

`wait_speech.params.phase_role: search_body` 要求明确的分类源；未配置即前置阻塞。支持的分类名为 search_bridge、search_body、chat、search_failure。目标段必须属于本轮绑定 trace、位于当前输入之后、仍是最新 worker 字幕段；外部标注还校验 run_id、segment_id、raw_line、文本 SHA256 和来源。旧段、其他会话及已过窗口的标注不能追触发。

```yaml
- kind: wait_speech
  name: D_body_active
  params:
    role: any
    phase_role: search_body
    timeout_s: 45
    require_sound: true
```

本机配置可合并 [D 的规则示例](../plans/r19/D-phase-rules.example.yaml)。外部文件则配置 `phase_review_file` 为本机私有路径，记录格式：

```json
{"schema_version":1,"run_id":"<当前运行>","trace":"<当前会话>","segment_id":"<当前段>","raw_line":198,"text_sha256":"<该行文本指纹>","role":"search_body","reviewer":"<评审者>","evidence_kind":"semantic_log_review"}
```

外部分类本身不发播放命令。执行器还要求同会话 worker_speaking=true 和麦克风活动达到阈值，随后播放预生成 WAV。麦克风活动没有说话者归属，播放器调用也不是物理起音；当前无法计算精确50%播放进度。

成功触发时，标注保存到运行 `phase_labels.jsonl` 与对应步骤 actual.phase_label；来源明确为 `semantic_log_review` 或 `rule_text_classification`，不得重命名为设备原生 role。实际发声、设备 ASR、旧播报是否停止、续讲语义须分开核对。D 增加了插话 ASR 等待，设备没记录插话则不跳过检查直接续讲。

## 运行与结果

[D-local-phase.yaml](../cases/r19-live/D-local-phase.yaml) 使用本机规则，[D-agent-phase.yaml](../cases/r19-live/D-agent-phase.yaml) 用于外部标注对照。两者准确话术沿用原 D；“播到一半”只在本轮按实际正文中途位置采证，未新增未经用户定义的50%量化验收门槛。

```bash
python qa.py library-prepare cases/r19-live/D-local-phase.yaml --output .local/prepared/<new-id>
python qa.py run cases/r19-live/D-local-phase.yaml --mode live --config config.local.yaml --prepared-assets .local/prepared/<new-id>/manifest.json --verbose
```

当前调用不自行开启90秒总计时；本轮真机通过外部90秒中断保护运行，每次使用独立录音。调用者应采用同等有界执行。实际结果与可复听入口见 [D 真机短报告](D_TRIGGER_VALIDATION_20261027.md)。
