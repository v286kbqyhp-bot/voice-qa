# 可复用合成语音库

`speech-library/list.yaml` 是准确文本到预生成WAV的目录；`speech-library/clips/` 保存可直接下载、跨环境播放的PCM16 WAV。首次落地包含R19 A–H全部已定义话术及唤醒、退出常用句。该目录随私人仓库版本化。

## 直接调用

clone或pull仓库后，按list中的`file`相对路径读取WAV即可。列表记录id、准确文本、voice、rate、locale、provider、sample_rate、channels、duration_s、sha256、bytes和origin。id由文本和合成配置确定，不以文件名猜文本。

```python
from voiceqa.speech_library import SpeechLibrary

asset = SpeechLibrary().ensure('我喜欢皮卡丘。')
print(asset['path'])       # 当前机器上的绝对WAV路径
print(asset['sha256'])     # 播放/传输前可核对
```

相同文本、音色、语速、语言、合成提供方会复用原文件；标点变化也视为不同输入。现有文件指纹不符时阻塞并要求恢复受信文件，不静默重生成覆盖。

## 缺失才补库

```bash
# 接受用例文件、目录或suite/catalog；不传inputs时默认cases/r19
python qa.py library-sync cases/r19
python qa.py library-sync plans/r19/catalog.yaml cases/r19-A-first-turn-debug.yaml

# 只准备，不播放、不录音；输出目录必须是新目录
python qa.py library-prepare cases/r19-A-second-turn-debug.yaml --output .local/prepared/r19-A2

# 测试时仅播放准备清单中的文件
python qa.py run cases/r19-A-second-turn-debug.yaml --mode live --config config.local.yaml --prepared-assets .local/prepared/r19-A2/manifest.json --verbose
```

当前默认合成profile为`macos_say / Tingting / 175 / zh-CN`。Windows/Linux可直接使用库中的WAV，无需相同系统音色。新增语句若需同一profile，可在Mac补库后提交推送，或通过已配置的Mac HTTP适配器补库：

```bash
python qa.py library-prepare <case.yaml> --output .local/prepared/<batch> --adapter-url http://127.0.0.1:8765 --provider macos_say
```

Token从`VQA_API_TOKEN`环境变量读取，不写入列表或Git。其他合成引擎可注入`synthesizer.prepare(params, output)`并显式设置provider/voice/rate；合成配置含义由该提供方保证，不在不同引擎间暗换音色或速率。

## 来源与Git门禁

历史导入仅接受本地真实运行的`tts/manifest.json`：必须`synthetic_fixture=false`、内容指纹匹配、WAV路径位于该运行的tts目录，并能从当次用例和运行环境核对准确文本及合成配置。回放占位音频、外部任意WAV和mic录音不自动入库。其他缺项离线合成后补入列表。

Git门禁只放行list明确列出、`origin=generated_tts`且文件、SHA256、格式和时长全部核验一致的库内WAV。未列出的音频、真实环境录音和大日志仍留在本机证据目录。列表更新采用进程间写锁和原子替换，防止多个准备进程丢失条目。

库目录中的WAV是预生成测试输入，不能作为设备实际回答或环境录音的证据。调用方仍需通过运行目录中的播放记录、设备事件和原始麦克风录音判断执行情况。
