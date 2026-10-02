# qianchuan-cv-shot-probe

本仓独立维护沙箱样本。修改前读 SKILL.md、README、packaging.json 和项目管理现行设计。
- 当前仅 A 阶段取视频，不引入 ASR/OCR/CV/模型分析。
- 迁移实现按 migration/source-manifest.json 维护，不能运行时导入兄弟仓。
- 测试用 python3.13 -B tools/test.py；打包用 python3.13 -B tools/build.py。
- 保留动态契约、授权、CSV/身份/周期门禁；一条、串行、无补位/重提。
- 来源/输出均在仓外，媒体、凭证、运行数据、环境不进入 Git/ZIP。
- 每次优化同步项目管理的唯一进度与总规划；提交/推送/发布按当前授权。
