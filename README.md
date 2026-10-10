# 千川 CV 分镜工作台 0.5.6

## 0.5.6运行产物路径（2026-10-08）

延续元技能的“用户工作对象目录与技能安装目录分离”，并采用脚本工作台的“业务数据根目录 + 唯一运行子目录”模式。执行前保持当前目录为用户工作对象目录，保存 `WORKSPACE_ROOT="$PWD"`，通过技能安装目录的绝对路径调用脚本；不得把技能安装目录当作工作对象目录。

`run-batch`、`probe-cv-batch`、单条 `acquire` 默认写入 `$WORKSPACE_ROOT/千川素材分镜数据/run-<UTC时间>-<8位随机标识>/`。`--output-root` 指定数据根目录，仍自动追加唯一 run 子目录；`--output-dir` 精确指定本次运行目录，不追加子目录，两项互斥。目录解析为绝对路径，拒绝技能安装目录及解析后指向它的软链接，拒绝复用已存在的新运行目录。返回的 `runDir` 为实际运行目录；批次还将其保存在 `batch.json`，最终报告以 `report.htmlPath` 为准。

批量最终报告在 `<runDir>/index.html`，选材记录在 `selection/`，新取数各条A/B输入与证据在 `runs/item-N/`，报告绑定快照在 `snapshots/`。复用已有A的批次仍从清单指定的原目录读取输入，并在那里保存本次CV attempt，新批次目录保存状态、快照和最终HTML。单条调试报告在 `<runDir>/report/index.html`。`export-html` 仍须显式 `--output-file`，建议导出到本次运行的 `exports/`；审计 `export-report` 仍须显式 `--output-dir`。`resume` 必须显式指定原 `--output-dir`，不自动另建目录。历史运行不移动、不改写；本次只调整路径，沿用现有A/B门禁、最终一HTML与低内存策略。

默认目录示意：

```text
用户工作对象目录/
  千川素材分镜数据/
    run-YYYYMMDD-HHMMSS-xxxxxxxx/
      index.html              # 批量最终阅读入口
      batch.json              # 状态、绝对runDir与逐条结果
      selection/              # 新取数选材记录
      runs/item-N/            # 新取数逐条A/B证据
      snapshots/item-N/       # 绑定本次结果的报告快照
```

## 0.5.5高缓存基线准入

cgroup v1 的原生 inactive_file 不含 swap-backed shmem；该 LRU 上的 LazyFree 页可丢弃，因此缺失 shmem 保留为缺失，不伪造0，也不再要求它参与 v1 扣减。dirty/writeback 同层字段缺失时，只接受可信原生 /proc/meminfo 的全局 Dirty/Writeback 两次观测较大值，作为保守扣除代理；保留原两行、时间窗和缺失清单，不把宿主全局值说成同层统计或空闲内存。源为软链接、覆盖挂载、非原生proc、权限/解析失败时无抵扣回退raw；v1 total与local不能混用，v2扣项要求保持。

本次明确改变95%原始占用的策略语义：v1从无条件停止改为缓存压力复核。仅可靠正inactive credit、工作集低于80%、原阶段预留和256MiB进程树预算均有余量、有效基线failcnt无新增、current under_oom=0且可读full PSI无显著压力/新增阻塞，才能准入；PSI缺失仍记unknown，由v1完整事件证据替代。实际占用达到沙箱limit始终停；v2及不可靠高raw仍按95%停。80/95/256数值和128/64/32MiB各阶段预留不变；阶段可用估算取95%余量与80%工作集停止线余量的较小值，并要求进程树剩余预算至少覆盖预留。headroomToSkillRawCeilingBytes保留原始口径，headroomForStageBytes/headroomBasis/cacheBackedAdmission说明估算。约200ms采样与两个观测点不能保证间隔内持续满足或拦截瞬时OOM；真实1GiB平台复测待完成。

## 0.5.4逐条释放与最终统一报告

批量A与B阶段仅落盘恢复/校验必需的输入、任务状态、CV镜头/帧、资源观察和收据，不生成逐条index.html或交付ZIP。每条B确认worker及子进程清理，校验结果并保存绑定本次attempt和SHA的JSON快照后，释放本次输入/代表帧/日志/快照文件的page cache建议并执行Python垃圾回收，再启动下一条；A结束后不再保留完整选材字典。失败停队列后可统一汇总已完成、失败和pending，内存守卫或清理未确认时停止自动报告尝试。

完整SHA核验保持：对已关闭的本次CSV/JSONL/MP4，分块读完即对已消费的整页作DONTNEED建议，读前仅一次fsync，EOF补充建议；保存完整字节数、SHA、初末文件身份/大小/修改时间与前后cgroup观察。文件变化仍拒绝，不省略身份/输入哈希；FFmpeg二进制、脚本和系统共享依赖走普通SHA，不对其缓存作建议。worker进程退出释放自身原生分配；gc.collect只处理不可达Python对象，DONTNEED为建议，两者均不保证共享沙箱占用下降或避免瞬时OOM。

全队列终止后才生成一份自包含HTML。最终渲染两遍读取绑定快照：首遍只保留小型导航/计数，第二遍逐条核验镜头和代表帧并流式写入，每条/每帧用后释放；图片上限仍单张256KiB、全报告12MiB。显式audit也等全部B完成后再导出，按快照绑定各次attempt，重复同A不会误导出最后一次结果。单条acquire/probe-cv/render-report兼容原调试报告；不删原视频/CSV/证据，不清全局缓存，不提高守卫，不引入ASR/模型分析或自动重试。

## 0.5.3详情RPA并行批次

参考千川元技能/工作台与云图元技能/工作台的最多3条在途及首批CSV门禁策略。run-batch按既定选材顺序划分每批≤3条：先依次发出本批提交请求并保存各taskId，不等待上一条完成；再按原顺序轮询、下载并核验本批全部CSV；之后串行下载/核验视频，本批成功才提交下一批。RPA远端最多3个在途任务，本地媒体与CV并发仍1；全部批次A就绪才开始串行B。

首批firstBatchCsvGate收齐结果：有未知/未提交结果为unconfirmed，全终态且至少一份有效CSV为passed，全无有效CSV为blocked。CV队列更严格，任一选中素材A失败都不提交新批、不启动B；即使首批gate passed也不能跳过失败素材继续。普通失败继续核实本批已保存taskId的其余任务，保留CSV/任务结果与失败前的有效A输入；不下载首个失败项之后的视频。队列守卫锁存后在提交、轮询、下载块及阶段边界合作停止本地新工作；执行中断保留原任务，保留在途证据，不能称远端任务已取消。未知提交保留submission_intent，不重提；collect/media只能复用已保存任务，缺taskId失败。

batch.json记录rpaConcurrency=3、mediaConcurrency=1、cvConcurrency=1（旧concurrency=1仍指CV）、rpaSubmissionCount、rpaWaves、acquisitionPhase与逐条rpaWave/rpaStatus/taskId；提交计数只计已确认taskId，未知请求不算确认成功。视频仍原SHA/有界尺寸/严格身份，CSV缓存绑定与哈希复验不放宽。数量1/短缺/最后不足3条按实际条数提交，不滚动补位、不重抓榜单、不自动重试或恢复。

独立源码：qianchuan-cv-shot-probe。0.5.6支持一次提问处理1–10条不同素材，一次榜单选材后先每批最多3条提交详情RPA并收齐CSV，串行完成视频下载/核验，再逐个执行CV并确认进程清理，默认一份可离线HTML。元技能/脚本工作台仅作设计参照，不运行时导入其代码，不改变这四个仓或插件。

入口见[SKILL.md](SKILL.md)。新取数run-batch、已有A串行probe-cv-batch、已有B轻量交付export-html；完整export-report ZIP仍是显式审计选项。现有详情素材数就是CV批量数量（不新增字段）；表单/pre_input数量范围、授权ID说明、默认交付同步。素材不足如实partial，失败停队列，pending保留，不自动恢复或补位。

HTML采用素材导航、大幅代表帧、原时间区间比例时间轴、完整镜头画廊、时长筛选、折叠执行摘要。JPEG单张256KiB、全报告12MiB预算，逐帧标明超限缺口；不嵌入视频、CSV、账号、签名URL或原始日志。导出校验已完成报告快照与CV镜头帧，避免重复读大媒体；原输入留运行目录可复验。

内存保护现行口径见0.5.5高缓存基线准入：工作集80%、原始95%复核线、进程树256MiB和阶段预留数值不变，95%语义有上述修正。约200ms队列/HTML观察和自身文件缓存建议不保证无瞬时OOM。CV原生方案、无ASR或模型分析。源榜单硬链接共享并哈希绑定，避免每条复制大报表。

开发验证：python3.12 -B tools/test.py；最小依赖python3.12 -B tools/test_native.py --require-minimal；显式packaging.json打包python3.12 -B tools/build.py。PRODUCT.md/DESIGN.md为源码设计上下文，不进入运行包。源码/包/平台验收分别记录于项目管理唯一进度。

0.5.3源视频长边上限1936（1920+16），总像素≤3,686,400，保持原1920×1920最大面积；支持1080×1922等轻微尺寸偏差。规则见config/media-input-policy.json，时长180秒/60fps/128MiB不变，原视频不裁剪或转码，CV仍最大边320。超限保存实际媒体参数/逐项准入原因，失败页直接显示实际值与上限。严格身份比较不使用尺寸容差；批次首败停与原内存保护保持。
