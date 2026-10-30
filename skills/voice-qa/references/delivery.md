# 本地交付与维护

最终交付是外部data_root内的原模板receipt.md、原始录音/字幕和独立GPT评审。`record`与`record-suite`也只导出本机records，不提交任何实测摘要到Git。先给用户简短结论和本地链接，需要录音/全文时直接给相应文件。

Git仅分发skill、runtime、接口/Schema、通用fixture与许可TTS库；pre-commit/pre-push检查实际快照，拒绝records/reports/sources/reviews等真实数据目录。旧数据迁移到本机archive，不重写Git历史；不要force-push。

维护使用`python -m unittest discover -s tests -q`、`VQA gate`和skill-creator的`quick_validate.py <skill-dir>`。接口变更同步README、INTERFACES、config.example、Schema及runtime contract；不再维护实测交付清单进仓库。启动器合同CLI v2；Windows复制skill后需同步runtime及本机配置。

Windows启动器按参数列表传递含空格/中文的路径，返回运行器退出码。Ctrl+C会先给运行器最多15秒封存录音；超时后终止进程，必要时再等待3秒强制结束。文本文件统一UTF-8，不依赖Windows系统默认编码。
