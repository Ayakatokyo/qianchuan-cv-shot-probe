# 千川视频CV分镜沙箱样本

稳定ID `qianchuan-cv-shot-probe`，当前0.3.0（A+B）。A修复已先提交并推送后才开发B；A门禁、整数时长容差、失败信息与完整导出保留。B默认复用视频执行受监督的FFmpeg原生镜头检测、逐镜头代表帧和技术报告。业务入口见[SKILL.md](SKILL.md)，参数及限制见[CV契约](references/cv-contract.md)。

- 依赖：Python3.12当前验证环境；同环境binary-only安装requirements-dev.txt用于开发，requirements.txt用于默认沙箱A+B；requirements-cv.txt仅用于显式adaptive对照。默认无重型CV导入，无ASR/OCR/模型。
- 入口：python scripts/run.py preflight --cv；probe-cv复用A运行目录，verify-cv检查收据；export-report/verify-export交付完整报告ZIP。
- 验证：python -B tools/test.py（42项，包括实际合成视频硬切、单镜头、VFR、损坏解码、代表帧失败、超时/SIGKILL/内存门禁及资源篡改）。合成案例不代表真实视频镜头质量或1GiB内存验收。
- 构建：python -B tools/build.py；packaging.json显式运行文件清单；开发工具/测试/CI不入运行包。
- 迁移来源见migration/source-manifest.json；本仓独立副本，运行时不引用兄弟仓。
- 原始客户附件留Downloads，运行目录、媒体、环境、凭证均在仓外，不进入Git。
- 0.2.0在目标沙箱导入阶段被技能80%总占用守卫主动终止；0.3.0已有目标沙箱单样本完整产物成功证据，三次稳定性与人工质量仍待验；不包含脚本语义对齐、Agent转写或宿主二次分析。

[唯一当前进度](../../项目管理/docs/AI短视频Skill开发批次进度.md) / [样本设计](../../项目管理/docs/规划/2026-10-03-千川云图CV分镜沙箱样本技能设计.md)。

内存守卫属于技能可配置策略；硬额度属于沙箱。0.3.0区分缓存、工作集估算、原始紧急上限、进程树预算和压力事件，见CV契约。最小依赖测试：python -B tools/test_native.py（41项，不含需要CV依赖的显式adaptive对照）。

## 内存优化前检查点（2026-10-04）

当前运行逻辑仍为0.3.0。本地真实A+B、正式CV收据与完整报告包校验已通过；千川素材报表CSV由用户下载后用正式解析器恢复原目录，没有重复提交API任务。macOS没有Linux cgroup额度/缓存指标，不能据此证明1GiB沙箱余量。下一步两平台同步阶段余量、资源观测及自身文件缓存管理，千川另优先减少重复结构化JSON和全量选材对象；不改原始证据/授权/身份/周期门禁，不引入模型或修改插件。具体运行证据见项目管理唯一进度和20261004-local-real-cv验证收据。
