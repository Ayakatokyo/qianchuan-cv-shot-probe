---
name: qianchuan-cv-shot-probe
description: 从千川素材榜单选择1–10条不同素材，先完成全部详情数据校验和视频下载，再逐个完成低内存镜头切分，交付内嵌代表帧的可视化报告。不进行音频转写或模型分析。
metadata:
  argument_hint: 选择授权账号、日期、素材数量和筛选条件，一次提问完成分镜。
---

# 千川视频CV分镜工作台（0.5.2）

默认先全部A、再串行B：一次查询素材榜单，按筛选排序选择1–10条不同素材，先逐条取得全部详情CSV、核对身份/周期并下载视频；所有选中素材的A输入就绪后，再按原顺序逐个执行低内存CV分镜并确认进程清理。只交付一份可视化HTML。音频转写、脚本时间码对齐和宿主AI二次分析后置。

## 输入与单次提问

按metadata.yaml读取授权表单，授权值使用extra.shop_id，不猜账号或历史凭证。api_shop为API店铺授权项；rpa_shop为RPA授权项，禁止把account广告主ID填入rpa_shop，程序解析广告主并核对API授权。日期北京时间T+2。

现有“详情素材数”（top_n）直接控制完整CV分镜批量，写入collection.targetTopN，不新增第二个数量参数。用户明确要求“3条素材”时写入3，不能降成1条。默认1条、范围1–10；超过10条说明单次上限，等待用户选定分批范围，不能静默截断。自然语言筛选转为本包注册表支持的条件，不明确则先澄清。默认整体消耗降序；candidateTopN必须与targetTopN相同（1–10），流式校验全部记录并有界选TOPN。契约与示例见[输入契约](references/input-contract.md)。

保持PWD，在用户工作对象目录写request.json，用安装根SKILL_ROOT定位本包脚本。依赖使用宿主既有Python环境与requirements.txt；缺依赖用已有依赖工具同环境binary-only安装，不安装模型。鉴权只用注入环境，不写入命令/日志/请求。外层执行超时留足完整队列（1条至少3600秒，批量按条数留足或分批），不因轮询快速结束把轮询耗时当业务耗时。预检与校验只做一次：

```bash
python "$SKILL_ROOT/scripts/run.py" preflight --cv
python "$SKILL_ROOT/scripts/run.py" validate --request-file "$WORKSPACE_ROOT/request.json"
python "$SKILL_ROOT/scripts/run.py" run-batch --request-file "$WORKSPACE_ROOT/request.json" --output-dir "$WORKSPACE_ROOT/千川分镜-唯一标识"
```

run-batch包含选材、A、B和轻量交付；数量1也用此入口。榜单仅一次，不能循环调用相同TOP1凑数量。每条详情CSV必须通过身份/周期校验，视频媒体辅助身份匹配后才记A就绪；全部A成功后才进入B，取数/下载和CV不会交替；B原生ffmpeg-scene、独立受监督worker、并发1，每条清理完成才下一条。同一沙箱两个平台也不得同时启动队列。用户已要求处理指定条数即可执行，不在每条之间重复询问确认。

## 输出与失败

默认交付返回的report.htmlPath及大小/哈希，一份自包含HTML可离线打开，内嵌代表帧，支持素材切换、镜头时间轴、大图、键盘切换和时长筛选。源视频/CSV/账号/签名URL/逐条日志留原运行目录，不使用报告归档器把整个目录打成大ZIP，不导出A中间技术包。CV只产生候选边界，人工质量待核对，不编造画面/口播/效果归因。图片预算12MiB、单张256KiB，超过预算逐帧显示缺口，镜头区间保持完整。

batch.json保存stage（selection/acquisition/cv/delivery/complete）、requestedCount/selectedCount/acquiredCount/completedCount和逐条acquisitionStatus/cvStatus、独立runDir/attempt、worker退出及清理、distinctVideoCount、整队列观测。素材不足返回partial/shortageCount，按实际选中数量执行，不能重新抓榜单或自动补位；重复视频哈希只能证明不同素材可能引用同视频，不能称多视频质量验收。A阶段首次失败停止全部后续取数且不启动任何CV，已就绪A的cvStatus仍pending；B阶段首次失败停止后续CV，全部A输入与已完成B保留，未处理B为pending；保留完成结果和诊断，不自动重试/恢复、重提未知任务或提高阈值。首批CSV blocked不得清除；未知提交只检查原taskId。

单条acquire保留为调试A入口且只接受数量1；批量输入会报use_run_batch，防止静默只取首条。resume仅用户明确恢复时复用原任务/请求/源哈希/绑定选择；队列无自动续跑CLI，不能换目录重取全部以绕过失败。导出失败但B已完成时优先以下轻量导出，不重跑B：

```bash
python "$SKILL_ROOT/scripts/run.py" export-html --run-dir "$RUN_ROOT" --output-file "$WORKSPACE_ROOT/交付/千川分镜-唯一标识.html"
```

export-html核对已生成报告快照、CV收据与镜头/代表帧SHA，不重新读取CSV/MP4或取数；旧报告快照若缺失或已改变须先inspect/render-report，不能伪造收据。运行目录html-exports保留约200ms采样、守卫和导出收据，默认不另给证据ZIP。高基线导致连轻量导出也被拦截时交付已保存诊断并停止。

## 复用已有A与审计选项

已有完整A输入、不需新榜单时用显式清单；缺视频的报告ZIP不是A输入。1–10项，每条只含完整A绝对runDir；重复同A用于稳定性时每条生成新attempt，独立快照绑定该次结果，不把B缓存复用算多次执行。

```json
{"schemaVersion":1,"runs":[{"runDir":"/绝对路径/完整A目录"}]}
```

```bash
python "$SKILL_ROOT/scripts/run.py" probe-cv-batch --manifest-file "$WORKSPACE_ROOT/已有输入.json" --output-dir "$WORKSPACE_ROOT/串行分镜-唯一标识"
```

默认最终只交付一份HTML。仅用户明确要求完整视频/内存审计证据时，probe-cv-batch可加--delivery audit，或调用export-report --run-dir --output-dir及verify-export --bundle-dir；审计ZIP包含MP4、A/B日志、全部帧、独立导出内存证据ZIP，可能再次触及缓存保护线，不能为了交付提高阈值。

## 资源保护与质量边界

源视频尺寸规则见config/media-input-policy.json：长边≤1936（1920+16），总像素≤3,686,400；允许1080×1922等小幅尺寸偏差，不放大原1920×1920最大面积。时长≤180秒、帧率≤60fps、文件≤128MiB保持。原始视频与SHA保留，CV仍缩放最大边320。尺寸容差仅用于资源准入，不用于CSV/媒体身份比较；超限保持media_input_limit，media/probe.json和失败报告记录实际值、上限及未执行身份比较的状态。

不安装NumPy/OpenCV/PySceneDetect等重型CV依赖；仅明确算法对照才binary-only安装requirements-cv.txt并显式adaptive。默认FFmpeg单线程、最大边320、原PTS、每镜头代表帧、最多300镜头，超限/缺帧失败不整段回退。工作集80%、原始占用95%、进程树256MiB以及压力/事件保护保持；扣减shmem/dirty/writeback任一同层字段缺失则raw保守回退，额度/压力不可读保持未知。阶段余量见config/memory-policy.json。

采样约200ms仍可能漏瞬时峰值，不保证绝不OOM。只有本次完成的显式普通文件尝试fsync/DONTNEED建议，不删源文件、不清全局缓存，不承诺腾出指定内存。批量选材源通过硬链接共享，不复制大榜单，绑定请求/源/每条选择，源变化会使所有依赖输入校验失败。三次稳定性、真实多素材及人工质量仍需平台证据；本地合成和HTML重绘不等于平台验收。详见[CV契约](references/cv-contract.md)。
