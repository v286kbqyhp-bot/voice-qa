# 本地交付与维护

最终交付是外部data_root内的原模板receipt.md、原始录音/标源文本和独立GPT评审。服务端文本模式明确“未运行独立ASR”，不可伪造ASR或实听栏。`record`与`record-suite`也只导出本机records，不提交任何实测摘要到Git。先给用户简短结论和本地链接，需要录音/全文时直接给相应文件。

交付前用`review-import --final`检查所有适用必测criterion已有pass/fail；存在blocked/inconclusive/not_run时先修复、补测，无法自补则与用户讨论具体缺口，不交最终回执。无--final仅为工作草稿。用户明确跳过/免验时，在原模板metadata.user_waivers按criterion id记录reason及authorization原意；不得自行移除原标准。功能、体验和后台职责分别判定，不把缺实听/计时或与本项无关的后台字段统统变成BLOCKED。

Git仅分发skill、runtime、接口/Schema、通用fixture与许可TTS库；pre-commit/pre-push检查实际快照，拒绝records/reports/sources/reviews等真实数据目录。旧数据迁移到本机archive，不重写Git历史；不要force-push。

维护使用`python -m unittest discover -s tests -q`、`VQA gate`和skill-creator的`quick_validate.py <skill-dir>`。接口变更同步README、INTERFACES、config.example、Schema及runtime contract；不再维护实测交付清单进仓库。启动器合同CLI v2；Windows复制skill后需同步runtime及本机配置。

Windows启动器按参数列表传递含空格/中文的路径，返回运行器退出码。Ctrl+C会先给运行器最多15秒封存录音；超时后终止进程，必要时再等待3秒强制结束。文本文件统一UTF-8，不依赖Windows系统默认编码。
