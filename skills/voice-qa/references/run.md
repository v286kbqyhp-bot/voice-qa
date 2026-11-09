# 执行与实时字幕

先核真实设备身份/版本、音频路由/音量、当前会话和其他执行进程，告诉用户本轮范围/话术。沿用用户已授权录音；不因查看旧录音而重新启动测试。

`VQA run /local/case.yaml --mode live --config /local/config.yaml --prepared-assets /local/prepared/manifest.json --verbose`。默认产物在data_root/runs，禁止输出到源码目录。原采集/步骤/日志都会封存；`VQA watch --device-log`只跟随，不开第二个执行器。

先在当轮做一条有界输入探针，检查当前绑定trace的`voice.tts_text_changed`，要求source=worker、segment_id、明确partial/final和replace语义；同时保留设备输入识别。核对分段是否完整，不能将dynamic_current等本地欢迎语当成云端答复。验证可用时设置`asr.live:false`，继续连续录音和抓全部事件，语义复核选择server_text。

服务端文本缺失或关键段不全时，再配置`asr.live:true`、ASR解释器和模型路径。独立进程跟随WAV写`asr/live.jsonl`，包含partial/final和主机观察时间；录音结束产生`asr/transcript.json`。已有录音可单独后处理，不必为了ASR再播一次。ASR不使用设备字幕作提示，也不在播音时机循环内调用GPT。初始化失败时保留阻塞；缺失证据不能生成占位字幕。

执行器等待本轮trace和明确条件，文件播完不等于设备收到。实体动作只在授权范围内，由用户观察；灯光/音量输入可按实际设备原路径播放，搜索/云端门不得掩盖“控制词根本未播放”。具体设备仍须核本地唤醒/输入门。

普通问答与打断后等待回复时，wait_output用after_input_final:true限定本次输入后的新回复分段。旧故事被打断后的迟到final不能充当数学新问题已答完；输入final缺失时阻塞并保留现场，不继续播下一句掩盖。静音/倒计时仍只作调度代理，不能签实际音频队列已空。

失败保留首轮和一次独立诊断，按用例依赖/停止条件决定后续；配置阻塞不盲重试，用户新授权复测另记。原生run-suite仅编排与采证，语义评审未配置时保持pending；当前GPT Agent应逐用例完成review后再决定是否诊断。明确退出不能以idle_timeout替代。

结束确认采集与房间已关闭，随后进入review；不凭execution=completed宣称通过。HTTP不自动具备本机实时ASR和联合等待能力，按接口声明处理。
