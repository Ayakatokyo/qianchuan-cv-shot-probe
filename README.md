# 千川 CV 分镜工作台 0.5.4

## 0.5.4逐条释放与最终统一报告

批量A与B阶段仅落盘恢复/校验必需的输入、任务状态、CV镜头/帧、资源观察和收据，不生成逐条index.html或交付ZIP。每条B确认worker及子进程清理，校验结果并保存绑定本次attempt和SHA的JSON快照后，释放本次输入/代表帧/日志/快照文件的page cache建议并执行Python垃圾回收，再启动下一条；A结束后不再保留完整选材字典。失败停队列后可统一汇总已完成、失败和pending，内存守卫或清理未确认时停止自动报告尝试。

完整SHA核验保持：对已关闭的本次CSV/JSONL/MP4，分块读完即对已消费的整页作DONTNEED建议，读前仅一次fsync，EOF补充建议；保存完整字节数、SHA、初末文件身份/大小/修改时间与前后cgroup观察。文件变化仍拒绝，不省略身份/输入哈希；FFmpeg二进制、脚本和系统共享依赖走普通SHA，不对其缓存作建议。worker进程退出释放自身原生分配；gc.collect只处理不可达Python对象，DONTNEED为建议，两者均不保证共享沙箱占用下降或避免瞬时OOM。

全队列终止后才生成一份自包含HTML。最终渲染两遍读取绑定快照：首遍只保留小型导航/计数，第二遍逐条核验镜头和代表帧并流式写入，每条/每帧用后释放；图片上限仍单张256KiB、全报告12MiB。显式audit也等全部B完成后再导出，按快照绑定各次attempt，重复同A不会误导出最后一次结果。单条acquire/probe-cv/render-report兼容原调试报告；不删原视频/CSV/证据，不清全局缓存，不提高守卫，不引入ASR/模型分析或自动重试。

## 0.5.3详情RPA并行批次

参考千川元技能/工作台与云图元技能/工作台的最多3条在途及首批CSV门禁策略。run-batch按既定选材顺序划分每批≤3条：先依次发出本批提交请求并保存各taskId，不等待上一条完成；再按原顺序轮询、下载并核验本批全部CSV；之后串行下载/核验视频，本批成功才提交下一批。RPA远端最多3个在途任务，本地媒体与CV并发仍1；全部批次A就绪才开始串行B。

首批firstBatchCsvGate收齐结果：有未知/未提交结果为unconfirmed，全终态且至少一份有效CSV为passed，全无有效CSV为blocked。CV队列更严格，任一选中素材A失败都不提交新批、不启动B；即使首批gate passed也不能跳过失败素材继续。普通失败继续核实本批已保存taskId的其余任务，保留CSV/任务结果与失败前的有效A输入；不下载首个失败项之后的视频。队列守卫锁存后在提交、轮询、下载块及阶段边界合作停止本地新工作；执行中断保留原任务，保留在途证据，不能称远端任务已取消。未知提交保留submission_intent，不重提；collect/media只能复用已保存任务，缺taskId失败。

batch.json记录rpaConcurrency=3、mediaConcurrency=1、cvConcurrency=1（旧concurrency=1仍指CV）、rpaSubmissionCount、rpaWaves、acquisitionPhase与逐条rpaWave/rpaStatus/taskId；提交计数只计已确认taskId，未知请求不算确认成功。视频仍原SHA/有界尺寸/严格身份，CSV缓存绑定与哈希复验不放宽。数量1/短缺/最后不足3条按实际条数提交，不滚动补位、不重抓榜单、不自动重试或恢复。

独立源码：qianchuan-cv-shot-probe。0.5.4支持一次提问处理1–10条不同素材，一次榜单选材后先每批最多3条提交详情RPA并收齐CSV，串行完成视频下载/核验，再逐个执行CV并确认进程清理，默认一份可离线HTML。元技能/脚本工作台仅作设计参照，不运行时导入其代码，不改变这四个仓或插件。

入口见[SKILL.md](SKILL.md)。新取数run-batch、已有A串行probe-cv-batch、已有B轻量交付export-html；完整export-report ZIP仍是显式审计选项。现有详情素材数就是CV批量数量（不新增字段）；表单/pre_input数量范围、授权ID说明、默认交付同步。素材不足如实partial，失败停队列，pending保留，不自动恢复或补位。

HTML采用素材导航、大幅代表帧、原时间区间比例时间轴、完整镜头画廊、时长筛选、折叠执行摘要。JPEG单张256KiB、全报告12MiB预算，逐帧标明超限缺口；不嵌入视频、CSV、账号、签名URL或原始日志。导出校验已完成报告快照与CV镜头帧，避免重复读大媒体；原输入留运行目录可复验。

内存保护保持工作集80%/原始95%/进程树256MiB和阶段余量；扣减字段缺失raw回退。约200ms队列/HTML观察和自身文件缓存建议不保证无瞬时OOM。CV原生方案、无ASR或模型分析。源榜单硬链接共享并哈希绑定，避免每条复制大报表。

开发验证：python3.12 -B tools/test.py；最小依赖python3.12 -B tools/test_native.py --require-minimal；显式packaging.json打包python3.12 -B tools/build.py。PRODUCT.md/DESIGN.md为源码设计上下文，不进入运行包。源码/包/平台验收分别记录于项目管理唯一进度。

0.5.3源视频长边上限1936（1920+16），总像素≤3,686,400，保持原1920×1920最大面积；支持1080×1922等轻微尺寸偏差。规则见config/media-input-policy.json，时长180秒/60fps/128MiB不变，原视频不裁剪或转码，CV仍最大边320。超限保存实际媒体参数/逐项准入原因，失败页直接显示实际值与上限。严格身份比较不使用尺寸容差；批次首败停与原内存保护保持。
