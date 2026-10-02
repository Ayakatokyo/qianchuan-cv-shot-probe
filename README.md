# 千川视频输入沙箱样本

稳定ID `qianchuan-cv-shot-probe`，A修复版0.1.1；用户提供的目标沙箱A日志显示两平台已取到视频，云图曾误判时长并在现场获授权修正恢复。本仓修复回归通过，修复包尚未在沙箱复测。业务入口和范围见[SKILL.md](SKILL.md)。

- 环境：Python 3.11+（本机使用3.13验证），按requirements.txt安装；无ASR/OCR/CV模型依赖。
- 入口：python3 scripts/run.py --help；preflight检查依赖；export-report/verify-export生成并核对含视频/资源的独立交付ZIP。
- 本地回归：python3.13 -B tools/test.py。
- 独立构建：python3.13 -B tools/build.py；正式清单见packaging.json。
- 迁移来源与快照见migration/source-manifest.json；本仓维护独立副本，运行时不引用兄弟仓。
- 输出到源码/安装目录之外的新路径；客户原件、环境、凭证不提交、不入包。
- A阶段input/CSV/MP4通过后，再实现probe-cv并在目标沙箱分别测试，不能跳过此门槛。

统一进度在[项目管理](../../项目管理/docs/AI短视频Skill开发批次进度.md)，设计在[双样本设计](../../项目管理/docs/规划/2026-10-03-千川云图CV分镜沙箱样本技能设计.md)。

A修复：整数秒时长容差、失败报告信息保留、收据去重、依赖预检、自包含交付与恢复活跃采样时长。源码/干净包各20项回归，原始客户附件留在Downloads，不进入Git。
