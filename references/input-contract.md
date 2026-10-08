# 0.5.4当前输入与交付契约

## 0.5.4逐条释放与最终统一报告

批量A与B阶段仅落盘恢复/校验必需的输入、任务状态、CV镜头/帧、资源观察和收据，不生成逐条index.html或交付ZIP。每条B确认worker及子进程清理，校验结果并保存绑定本次attempt和SHA的JSON快照后，释放本次输入/代表帧/日志/快照文件的page cache建议并执行Python垃圾回收，再启动下一条；A结束后不再保留完整选材字典。失败停队列后可统一汇总已完成、失败和pending，内存守卫或清理未确认时停止自动报告尝试。

完整SHA核验保持：对已关闭的本次CSV/JSONL/MP4，分块读完即对已消费的整页作DONTNEED建议，读前仅一次fsync，EOF补充建议；保存完整字节数、SHA、初末文件身份/大小/修改时间与前后cgroup观察。文件变化仍拒绝，不省略身份/输入哈希；FFmpeg二进制、脚本和系统共享依赖走普通SHA，不对其缓存作建议。worker进程退出释放自身原生分配；gc.collect只处理不可达Python对象，DONTNEED为建议，两者均不保证共享沙箱占用下降或避免瞬时OOM。

全队列终止后才生成一份自包含HTML。最终渲染两遍读取绑定快照：首遍只保留小型导航/计数，第二遍逐条核验镜头和代表帧并流式写入，每条/每帧用后释放；图片上限仍单张256KiB、全报告12MiB。显式audit也等全部B完成后再导出，按快照绑定各次attempt，重复同A不会误导出最后一次结果。单条acquire/probe-cv/render-report兼容原调试报告；不删原视频/CSV/证据，不清全局缓存，不提高守卫，不引入ASR/模型分析或自动重试。

## 0.5.3详情RPA并行批次

参考千川元技能/工作台与云图元技能/工作台的最多3条在途及首批CSV门禁策略。run-batch按既定选材顺序划分每批≤3条：先依次发出本批提交请求并保存各taskId，不等待上一条完成；再按原顺序轮询、下载并核验本批全部CSV；之后串行下载/核验视频，本批成功才提交下一批。RPA远端最多3个在途任务，本地媒体与CV并发仍1；全部批次A就绪才开始串行B。

首批firstBatchCsvGate收齐结果：有未知/未提交结果为unconfirmed，全终态且至少一份有效CSV为passed，全无有效CSV为blocked。CV队列更严格，任一选中素材A失败都不提交新批、不启动B；即使首批gate passed也不能跳过失败素材继续。普通失败继续核实本批已保存taskId的其余任务，保留CSV/任务结果与失败前的有效A输入；不下载首个失败项之后的视频。队列守卫锁存后在提交、轮询、下载块及阶段边界合作停止本地新工作；执行中断保留原任务，保留在途证据，不能称远端任务已取消。未知提交保留submission_intent，不重提；collect/media只能复用已保存任务，缺taskId失败。

batch.json记录rpaConcurrency=3、mediaConcurrency=1、cvConcurrency=1（旧concurrency=1仍指CV）、rpaSubmissionCount、rpaWaves、acquisitionPhase与逐条rpaWave/rpaStatus/taskId；提交计数只计已确认taskId，未知请求不算确认成功。视频仍原SHA/有界尺寸/严格身份，CSV缓存绑定与哈希复验不放宽。数量1/短缺/最后不足3条按实际条数提交，不滚动补位、不重抓榜单、不自动重试或恢复。

run-batch接受平台原QuerySpec与授权项，数量1–10；acquire仅数量1，批量拒绝use_run_batch。一次选材队列按已确认排序选择不同ID，数量不足标partial/shortageCount，绝不补位。千川targetTopN=candidateTopN，云图target_top_n≤10且candidate_top_n=1000。每条绑定原请求/共享源SHA/选择与队列SHA，源硬链接不复制报表；任意源变化停止。首批最多3条详情CSV必须收齐并核验真实、可解析、身份/周期匹配；失败/未知/blocked不提交新批次。先每批最多3条提交详情RPA并核验本批CSV、串行下载/核验视频，全部批次A就绪，再按原顺序串行B；A失败不启动CV，B失败保留全部A和已完成B。batch.json区分stage、acquiredCount、completedCount与逐条acquisitionStatus/cvStatus。授权、日期、字段语义沿用以下既有契约。

默认交付内嵌代表帧的单HTML；export-html校验已完成report.json快照收据及CV收据/镜头/帧，不读CSV或MP4，不具备重新验证完整A能力；完整A审计仍用verify，视频输入使用probe-cv时仍全验证。未提供完整A输入不能从HTML恢复B。旧export-report为显式审计大包选项，不能因为默认只有HTML称交付缺视频。旧节中“必须完整ZIP”为历史技术包规则，默认已由本节替代。

# 输入与恢复契约

request.json 包含 query_spec 及所选授权 ID；使用 config/request-example.json 的结构，示例日期需按本次用户输入替换。字段注册表来自本包config，不用另装元技能。

千川：api_shop、rpa_shop。query_spec.scope.shopId 由 api_shop 写入，advertiserId 字段可省略，程序先做语法校验占位再以真实授权广告主解析值执行；不把占位ID提交给报表/RPA。collection.targetTopN/candidateTopN 必须都是1。

元技能的完整日期、筛选/排序语义沿用；样本取消高光规则，只准备输入。CLI acquire 涉及授权线上取数，validate/status/verify/render-report为本地操作。未知状态只检查既有task ID；resume是用户明确恢复后的入口，不自动调用。

输出：request/environment/status JSON，acquisition任务/CSV/选材/来源/收据，media原视频与下载收据，resources.ndjson和report完整技术报告。原CSV/签名URL保留私有；报告不显示账号URL或凭证。视频最大128MiB，CSV/报表文件32MiB，时长≤180秒，长边≤1936且总像素≤3,686,400，帧率≤60。超限终止不换素材。CSV有身份和周期但缺URL归为视频来源缺口，不误报为入参CSV无效。

A阶段资源记录为当前Python进程RSS/生命周期高水位、Linux进程树采样及可见cgroup指标；一秒采样可能遗漏短时峰值，历史memory.peak不代表本次峰值。macOS无Linux cgroup时记录不可用，不伪造容器额度。阶段完成为video_ready，A完成时CV标not_run，B结果独立记录。

0.1.1补充：preflight不需HTTP依赖即可给缺依赖短状态；先在同一Python环境安装依赖，再validate/acquire。media/probe.json在身份辅助比较之前落盘，失败报告可显示实测值和比较容差，status仍failed。整数秒元数据±1秒，小数秒0.1秒，维度/FPS严格，时长0代表缺失。成功receipt按相对路径去重。

export-report生成仓外独立目录和同名ZIP，含report、合法下载视频、resources.ndjson及bundle-manifest；verify-export校验完整文件集合、资源引用与哈希。原CSV/URL/请求不导出。实际下载ZIP若只有report即不完整，不能用于CV输入或内存原始采样核查。activeSampledSec汇总同执行的连续采样段，不把resume间隔当活跃耗时；elapsedObservedSec仍表示首尾墙钟跨度。

## 0.4.0恢复与内存补充

阶段启动余量不足返回paused/insufficient_stage_headroom，phase-memory.json/ndjson保留判定，已绑定选择/源文件和tasks不清除。resume需明确用户授权，原request、selection-source哈希和首批CSV门禁仍校验；已有selection.json复用，不重新选材。CSV blocked及未知提交仍禁止自动重提。原始数据及历史检查点不因缓存建议被删除；fsync/DONTNEED只为建议，不承诺腾出沙箱容量。新恢复路径仅在已保存选择后复用选材结果；报表下载失败的人工CSV导入尚无正式CLI，继续保留既有私有恢复证据，不自动重提API。

files报表的新规范检查点是原始CSV，流式验证全部必需指标后保存field-manifest和result-parse状态；selection-source绑定原CSV。单条解析记录上限2Mi字符（CSV解析器自身字段限制仍适用），最多输入32MiB。有界TOPN（1–10）保留全表重复ID/过滤排除计数及排序ID平局语义。旧JSON数组流式兼容，直接内联payload继续兼容而非完整流式响应。

恢复选择的快捷路径要求0.4.0 selection-binding.json同时绑定原请求、selection-source和selection.json内容；任何变化停止。旧目录缺此绑定时用流式兼容路径重算原选择并比较，不把未绑定的选择文件直接信任为已验证。

## 0.5.3千川源视频尺寸准入

config/media-input-policy.json：长边≤1936（比1920增加16），总像素≤3,686,400，不扩大原规则隐含的最大面积；保持180秒/60fps/128MiB及所有内存阈值。CV处理仍最大边320，无原视频裁剪/转码。probe.json保存原始宽高/视频SHA与admission（policy、observed、violations、dimensionToleranceUsed），尺寸超限media_input_limit会保存媒体信息与validation=not_run，HTML直接显示实际值/上限；成功A收据绑定原媒体，宽高身份比较保持精确匹配。未新增队列续跑/跳过失败。
