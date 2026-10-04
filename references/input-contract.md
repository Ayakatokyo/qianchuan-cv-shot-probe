# 输入与恢复契约

request.json 包含 query_spec 及所选授权 ID；使用 config/request-example.json 的结构，示例日期需按本次用户输入替换。字段注册表来自本包config，不用另装元技能。

千川：api_shop、rpa_shop。query_spec.scope.shopId 由 api_shop 写入，advertiserId 字段可省略，程序先做语法校验占位再以真实授权广告主解析值执行；不把占位ID提交给报表/RPA。collection.targetTopN/candidateTopN 必须都是1。

元技能的完整日期、筛选/排序语义沿用；样本取消高光规则，只准备输入。CLI acquire 涉及授权线上取数，validate/status/verify/render-report为本地操作。未知状态只检查既有task ID；resume是用户明确恢复后的入口，不自动调用。

输出：request/environment/status JSON，acquisition任务/CSV/选材/来源/收据，media原视频与下载收据，resources.ndjson和report完整技术报告。原CSV/签名URL保留私有；报告不显示账号URL或凭证。视频最大128MiB，CSV/报表文件32MiB，时长≤180秒，两边≤1920，帧率≤60。超限终止不换素材。CSV有身份和周期但缺URL归为视频来源缺口，不误报为入参CSV无效。

A阶段资源记录为当前Python进程RSS/生命周期高水位、Linux进程树采样及可见cgroup指标；一秒采样可能遗漏短时峰值，历史memory.peak不代表本次峰值。macOS无Linux cgroup时记录不可用，不伪造容器额度。阶段完成为video_ready，A完成时CV标not_run，B结果独立记录。

0.1.1补充：preflight不需HTTP依赖即可给缺依赖短状态；先在同一Python环境安装依赖，再validate/acquire。media/probe.json在身份辅助比较之前落盘，失败报告可显示实测值和比较容差，status仍failed。整数秒元数据±1秒，小数秒0.1秒，维度/FPS严格，时长0代表缺失。成功receipt按相对路径去重。

export-report生成仓外独立目录和同名ZIP，含report、合法下载视频、resources.ndjson及bundle-manifest；verify-export校验完整文件集合、资源引用与哈希。原CSV/URL/请求不导出。实际下载ZIP若只有report即不完整，不能用于CV输入或内存原始采样核查。activeSampledSec汇总同执行的连续采样段，不把resume间隔当活跃耗时；elapsedObservedSec仍表示首尾墙钟跨度。

## 0.4.0恢复与内存补充

阶段启动余量不足返回paused/insufficient_stage_headroom，phase-memory.json/ndjson保留判定，已绑定选择/源文件和tasks不清除。resume需明确用户授权，原request、selection-source哈希和首批CSV门禁仍校验；已有selection.json复用，不重新选材。CSV blocked及未知提交仍禁止自动重提。原始数据及历史检查点不因缓存建议被删除；fsync/DONTNEED只为建议，不承诺腾出沙箱容量。新恢复路径仅在已保存选择后复用选材结果；报表下载失败的人工CSV导入尚无正式CLI，继续保留既有私有恢复证据，不自动重提API。

files报表的新规范检查点是原始CSV，流式验证全部必需指标后保存field-manifest和result-parse状态；selection-source绑定原CSV。单条解析记录上限2Mi字符（CSV解析器自身字段限制仍适用），最多输入32MiB。有界TOP1保留全表重复ID/过滤排除计数及排序ID平局语义。旧JSON数组流式兼容，直接内联payload继续兼容而非完整流式响应。

恢复选择的快捷路径要求0.4.0 selection-binding.json同时绑定原请求、selection-source和selection.json内容；任何变化停止。旧目录缺此绑定时用流式兼容路径重算原选择并比较，不把未绑定的选择文件直接信任为已验证。
