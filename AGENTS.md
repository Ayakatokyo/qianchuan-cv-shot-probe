# qianchuan-cv-shot-probe

本仓独立维护沙箱样本。修改前读 SKILL.md、README、packaging.json 和项目管理现行设计。
- 当前A取视频+B低内存CV；不引入ASR/OCR/模型分析。CV必须复用A已校验输入，由独立受监督worker执行。
- 迁移实现按 migration/source-manifest.json 维护，不能运行时导入兄弟仓。
- 测试用 python3.12 -B tools/test.py；打包用 python3.12 -B tools/build.py。
- 保留动态契约、授权、CSV/身份/周期门禁；run-batch一次榜单选1–10条不同素材，先每批最多3条详情RPA、收齐CSV并串行下载/核验视频，全部A就绪后串行B分镜，无补位/自动重提；acquire单条保留，probe-cv-batch复用显式已有A。默认只交付可视化HTML，完整ZIP仅显式审计。
- 来源/输出均在仓外，媒体、凭证、运行数据、环境不进入 Git/ZIP。
- 每次优化同步项目管理的唯一进度与总规划；提交/推送/发布按当前授权。


- 0.5.4批量内部延后报告：A/B只落必要输入、镜头/帧、状态、收据和SHA绑定JSON快照；全部队列终止后才统一HTML，audit也后置。每条最终校验/摘要/快照后释放本次owned输入及精确attempt文件page cache建议和不可达Python对象，确认进程清理才下一条；守卫/清理未确认禁止自动补报告。
- 自有CSV/JSONL/MP4完整SHA使用显式分块页缓存建议并记录初末文件身份和全字节覆盖；不对系统binary/依赖无差别建议，不减少哈希/身份门禁、不删原件、不改额度/阈值。DONTNEED和GC都不保证内存下降，真实1GiB/多素材仍需平台证据。
