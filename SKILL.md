---
name: qianchuan-cv-shot-probe
description: 用于千川单视频CV沙箱测试；授权取数、RPA CSV校验后下载视频，复用本地视频执行低内存镜头切分、代表帧和资源报告。无ASR或模型分析。
metadata:
  argument_hint: 使用平台授权表单，获取1条视频并测试低内存CV分镜。
---

# 千川视频CV分镜沙箱测试（A+B阶段）

当前实现A视频输入与B低内存CV分镜。只获取一条视频，不补位、不自动重提任务。B复用A已校验视频；音频转写、脚本拆解、宿主二次分析后置。

## 输入准备

按 metadata.yaml 的表单读取授权项，取 extra.shop_id；禁止猜账号或搜历史凭证。日期采用北京时间T+2。样本数量固定1；若表单预填大于1但用户明确要求只取1条，告知冲突并按1构造请求，不能把大于1的值提交给程序。自然语言筛选先转换为本包字段注册表支持的结构化条件；不明确的条件不得猜测。请求结构和示例见[输入与恢复契约](references/input-contract.md)。

在用户工作对象目录生成 request.json，保持 PWD，不切换到技能目录。产物写入仓外的新目录。千川 API 店铺和 RPA 授权项 ID 属于不同 ID 域；广告主 ID 由程序解析并核对授权，不要求用户额外填写。

## 执行

先说明取数、CSV校验、视频下载、媒体探测及报告的阶段顺序；外层执行超时设置3600秒。通过技能安装根目录定位脚本与依赖，用宿主预装环境或既有依赖工具按 requirements.txt 运行，不安装模型。先运行 preflight 检查依赖；若缺失，用宿主依赖工具在同一 Python 环境按 requirements.txt 安装（如 uv pip install --python <当前python路径> --only-binary=:all: -r <技能requirements.txt>），成功后继续。多行辅助脚本先保存文件再执行；常规校验和交付使用本包 CLI。鉴权仅使用宿主注入环境，不在命令行/日志/请求文件写凭证。

```bash
python "$SKILL_ROOT/scripts/run.py" preflight
python "$SKILL_ROOT/scripts/run.py" validate --request-file "$WORKSPACE_ROOT/request.json"
python "$SKILL_ROOT/scripts/run.py" acquire --request-file "$WORKSPACE_ROOT/request.json" --output-dir "$WORKSPACE_ROOT/千川CV分镜测试/run-唯一标识"
```

validate只做本地校验，失败不调用外部服务。acquire完成动态契约、账号、CSV身份/周期校验、视频下载与探测，生成 report/index.html，返回 video_ready 仅代表A阶段输入完成。模型不能把此状态称为CV、Agent或沙箱全链路测试成功。签名URL和账号仅留在私有来源文件，不复制到报告或对话。

## 失败与恢复

```bash
python "$SKILL_ROOT/scripts/run.py" status --run-dir "$RUN_ROOT"
python "$SKILL_ROOT/scripts/run.py" verify --run-dir "$RUN_ROOT"
python "$SKILL_ROOT/scripts/run.py" render-report --run-dir "$RUN_ROOT"
```

失败后先交付具体阶段、错误码、已有文件和诊断报告，再停下。首批CSV blocked不得原地清除，修正输入后用新目录；unknown/提交记录无taskId时不得重新提交。只有用户明确要求恢复，才运行 resume（参数与 acquire 相同），它复用原任务和原素材，哈希/输入改变即停止。

报告中的视频为相对本地资源，交付完整运行报告及关联媒体，不单独复制HTML。成功acquire已生成并校验A阶段收据，不重复例行verify；文件变化或用户要求审计才调用verify。资源采样是观察值，cgroup不可读时内存额度/余量未知；本机输入测试不能替代生意高手真实沙箱测试。

## 技术报告交付

```bash
python "$SKILL_ROOT/scripts/run.py" export-report --run-dir "$RUN_ROOT" --output-dir "$WORKSPACE_ROOT/交付/样本报告-唯一标识"
python "$SKILL_ROOT/scripts/run.py" verify-export --bundle-dir "$WORKSPACE_ROOT/交付/样本报告-唯一标识"
```

export-report只选入报告、源视频（有合法下载收据时）、A/B资源、CV状态/收据/依赖/诊断/配置/镜头/全部代表帧及bundle-manifest，不包含原CSV、账号、请求或签名URL。交付生成的ZIP文件及其SHA；不能只用报告归档器导出report子目录后称为含源视频。以实际下载ZIP文件清单为准，至少检查manifest、report、media和resources；缺项说明交付不完整。失败态报告保留已完成的素材与实测媒体，但不生成成功收据。时长按元数据精度核对：整数秒允许1秒差，小数秒默认0.1秒，宽高/FPS仍严格；比较值/容差写入报告。

## B阶段：复用视频测试分镜（0.4.1）

A的video_ready和成功收据通过后执行。已有运行目录含原视频/CSV即可复用；只有report的历史ZIP不能当B输入，不能因为缺视频自动重提RPA。默认FFmpeg原生方案复用A依赖，不需要安装NumPy/OpenCV/PySceneDetect；在执行A的同一Python环境运行：

```bash
uv pip install --python <当前python路径> --only-binary=:all: -r "$SKILL_ROOT/requirements.txt"
python "$SKILL_ROOT/scripts/run.py" preflight --cv
python "$SKILL_ROOT/scripts/run.py" probe-cv --run-dir "$RUN_ROOT" --profile low-memory --backend ffmpeg-scene
python "$SKILL_ROOT/scripts/run.py" verify-cv --run-dir "$RUN_ROOT"
python "$SKILL_ROOT/scripts/run.py" export-report --run-dir "$RUN_ROOT" --output-dir "$WORKSPACE_ROOT/交付/CV样本报告-唯一标识"
python "$SKILL_ROOT/scripts/run.py" verify-export --bundle-dir "$WORKSPACE_ROOT/交付/CV样本报告-唯一标识"
```

宿主没有uv时使用既有依赖工具，同环境binary-only安装；不能改装模型或偷偷切到其他环境。preflight只核对版本，真实import也在监测worker内。probe-cv默认300秒，外层执行超时留至少360秒，逐步监测；B失败先status/render-report并交付诊断，停止后续分析，不把A成功当成CV成功。

默认FFmpeg scdet最大边320、单线程、保留原PTS，Python只接收元数据；每镜头有代表帧，上限300，超限失败。守卫是技能策略，1GiB等硬额度来自沙箱：工作集估算80%、原始占用95%、进程树256MiB及压力/事件共同保护；缓存扣减不是保证可用内存。额度/进程树未知明示，SIGKILL不自动标OOM。具体参数、资源限制和产物见[CV契约](references/cv-contract.md)。结果是算法候选边界，需人工核对真实镜头质量。

用户要求稳定性测试时，以同一视频/配置显式执行 --attempt-id repeat-1、repeat-2、repeat-3；各次保持独立收据和资源，不重取数。普通重复命令复用已校验成功B，不能把缓存复用算成三次测试。每平台必须返回实际含视频/代表帧/资源的完整ZIP；安装成功、本地通过都不能替代目标沙箱验收。本阶段不调用宿主Agent分析。

需要显式算法对照时，在同环境binary-only安装requirements-cv.txt，再使用preflight --cv --backend adaptive和probe-cv --backend adaptive；默认流程不安装重型CV依赖，也不自动切换算法。真实切点质量需分别核对。

## 0.4.0阶段余量与缓存管理

读取config/memory-policy.json；相对原总占用95%停止线，A选材预留128MiB、详情RPA/下载各32MiB、媒体探测64MiB，B启动预留128MiB，报告导出32MiB。均为技能试验策略，不能提高阈值绕过保护。A返回paused/insufficient_stage_headroom时交付已有状态和phase-memory诊断，停止后续取数；用户明确要求后才resume复用已绑定选择和任务。B余量不足不启动worker；等待环境余量足够后，在用户授权下以新attempt-id重试，不重取A。

完成选材/CSV/视频等阶段后，只对当前运行目录acquisition/media内完成的普通CSV/JSON/视频尝试fsync及POSIX_FADV_DONTNEED，不删除源文件或清全局缓存。建议可能不受支持/被忽略，cache-advice.ndjson记录支持状态、字节数及前后cgroup观察；差值可能含其他进程，不能宣称已释放指定内存。cgroup不可读时额度/余量保持未知；Mac仅提供进程树RSS对照。

报告汇总观测到的rawUsage/工作集估算/cache/anon峰值。memory-admission.json保存B启动判定，guard-samples.ndjson保存约200ms守卫观察，不能用最后一次memory-guard代替全过程峰值。继续保留工作集80%、总占用95%、进程树256MiB和压力事件保护；逐阶段余量不是峰值上限或OOM保证。

千川files报表按原始单列JSON CSV流式校验全部记录并有界选TOP1，不生成两份全量结构化JSON；原CSV作为哈希绑定检查点。仍保留必需指标、重复ID、筛选排序及CSV身份/周期校验。旧report-records.json用流式JSON数组读取兼容；旧文件不自动删除。网关直接返回内联payload时仍兼容全量对象，该路径不宣称已流式化。

恢复选择的快捷路径要求0.4.0 selection-binding.json同时绑定原请求、selection-source和selection.json内容；任何变化停止。旧目录缺此绑定时用流式兼容路径重算原选择并比较，不把未绑定的选择文件直接信任为已验证。

## 0.4.1保守守卫、导出证据与串行验收

任一同口径shmem/dirty/writeback字段缺失时，不抵扣inactive_file，工作集按raw判断；缺失字段保留在诊断。不改80%工作集/95%原始占用/256MiB进程树或阶段余量；高基线阻断先交付诊断，不调线强行执行。

export-report新增本次生成的副本/JPEG/ZIP缓存建议及全程约200ms采样；复制和ZIP以1MiB块检查，阶段间也检查，守卫停止即中止后续发布。缓存建议只作用本次显式文件，原报告/媒体保留。返回完整报告ZIP和memoryEvidenceZip（`.memory.zip`）：后者包含独立resources、guard-samples、cache-advice、status及receipt，绑定报告ZIP SHA。报告ZIP内A资源/缓存记录仍是其原阶段快照，导出全过程以独立memory证据为准；证据ZIP自身打包不在监测覆盖内。失败也保留`.memory`和证据ZIP，查看outputPublished区分发布前失败和发布后末次观测失败。

只有用户要求串行验收/处理已有输入时使用probe-cv-batch。在工作对象目录保存清单，使用已存在且完整的A目录绝对路径；禁止从报告ZIP伪造A或为了填队列重提取数。数量1–10，重复同目录用于稳定性，多个不同目录用于已有素材批量。例：

```json
{"schemaVersion":1,"runs":[{"runDir":"/绝对路径/原A目录"},{"runDir":"/绝对路径/原A目录"},{"runDir":"/绝对路径/原A目录"}]}
```

```bash
python "$SKILL_ROOT/scripts/run.py" probe-cv-batch --manifest-file "$WORKSPACE_ROOT/serial-inputs.json" --output-dir "$WORKSPACE_ROOT/串行CV验收-唯一标识"
```

固定ffmpeg-scene、并发1，每条强制新attempt并等待worker清理完成、报告导出结束；同沙箱千川/云图不得同时跑两条队列。首次失败立即停止，batch.json保存已完成和pending，不自动重试/恢复。返回独立B收据、每条报告与memory ZIP、整队列resources/guard-samples和batch.json。已完成结果可保留；修复阻断后仅在用户授权下用新目录显式列出待执行输入。distinctVideoCount=1只能证明同视频重复，不能称多素材验收。当前没有榜单一次取数的正式批量详情/下载入口，没有ASR或宿主AI分析；离线合成通过不替代真实1GiB多素材/质量验收。
