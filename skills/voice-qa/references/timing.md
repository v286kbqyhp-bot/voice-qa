# 逐轮计时

Native运行在结束采集后自动生成`timing.json`，与WAV、播放记录、事件一起封存。新录音保存PortAudio首样本时钟，映射到主机单调钟；旧录音用已有采集锚点，并通过预生成输入音频与麦克风录音的能量包络匹配修正偏移。

旧封存运行可补算，不改写原包：

```sh
VQA timing /local/runs/RUN --output /local/work/NEW/timing.json
```

每个实际播放步骤单独记录主机播放起止、当前trace、输入final、ready或ready_fallback、首条worker文本和排除等待词后的文本响应时间。事件只用同一主机单调钟相减；字幕到达时间不是声音首音。迟到的旧segment和旧房文本不得进入新问句计时。

YAML播放步骤可设params.timing_role为wake/input/control/off。新用例给唤醒标wake、提问标input、再见等控制词标control；control/off不计算正文响应。旧用例兼容按wake步骤名识别唤醒，cleanup自动按control处理。

录音估算先定位已播输入在WAV中的有效末尾，去掉尾部静音，再结合当前trace与文本分段寻找录音中的回答起音。回执用“约X秒（录音估算，按0.5秒取整）”，同时引用WAV位置和方法；说话人归属来自测试时序与日志推断。取整单位不是误差保证，未标定的误差不能承诺为±0.5秒。能量门仅是候选，不独立证明语义或设备身份。存在旁人、叠播、长前导词或连续跨段时要复核边界，必要时补测，不能把候选当已校准T5。

发现首段只是“稍等”“好呀”或前导闲聊时，按实际返回文本复核正文分段。可传`--policy /local/timing-policy.json`：

```json
{"audio_sha256":"对应录音SHA256","steps":{"question":{"body_segment_ids":["实际正文segment_id"]}}}
```

policy只选择已有同trace分段，不生成回答。录音匹配失败、被打断或没有对应回复时保留具体原因，查播放文件、路由、录音质量和事件关联；修复后重算或定向补测，再交付。用户明确允许不验的声学项单列其决定，不升级成功能阻塞。
