# 执行与实时字幕

先核真实设备身份/版本、音频路由/音量、当前会话和其他执行进程，告诉用户本轮范围/话术。沿用用户已授权录音；不因查看旧录音而重新启动测试。

`VQA run /local/case.yaml --mode live --config /local/config.yaml --prepared-assets /local/prepared/manifest.json --verbose`。默认产物在data_root/runs，禁止输出到源码目录。原采集/步骤/日志都会封存；`VQA watch --device-log`只跟随，不开第二个执行器。

配置`asr.live:true`、ASR解释器和模型路径后，独立进程跟随WAV写`asr/live.jsonl`，包含partial/final和主机观察时间；录音结束产生`asr/transcript.json`。ASR不使用设备字幕作提示，也不在播音时机循环内调用GPT。初始化失败时不继续播用例输入；后续字幕失败保留原音频，产品待判。

执行器等待本轮trace和明确条件，文件播完不等于设备收到。实体动作只在授权范围内，由用户观察；灯光/音量输入可按实际设备原路径播放，搜索/云端门不得掩盖“控制词根本未播放”。具体设备仍须核本地唤醒/输入门。

失败保留首轮和一次独立诊断，按用例依赖/停止条件决定后续；配置阻塞不盲重试，用户新授权复测另记。原生run-suite仅编排与采证，语义评审未配置时保持pending；当前GPT Agent应逐用例完成review后再决定是否诊断。明确退出不能以idle_timeout替代。

结束确认采集与房间已关闭，随后进入review；不凭execution=completed宣称通过。HTTP不自动具备本机实时ASR和联合等待能力，按接口声明处理。
