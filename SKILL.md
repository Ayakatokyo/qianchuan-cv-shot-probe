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

## B阶段：复用视频测试CV

A的video_ready和成功收据通过后执行。已有运行目录含原视频/CSV即可复用；只有report的历史ZIP不能当B输入，不能因为缺视频自动重提RPA。CV依赖安装到执行A的同一Python环境：

```bash
uv pip install --python <当前python路径> --only-binary=:all: -r "$SKILL_ROOT/requirements-cv.txt"
python "$SKILL_ROOT/scripts/run.py" preflight --cv
python "$SKILL_ROOT/scripts/run.py" probe-cv --run-dir "$RUN_ROOT" --profile low-memory
python "$SKILL_ROOT/scripts/run.py" verify-cv --run-dir "$RUN_ROOT"
python "$SKILL_ROOT/scripts/run.py" export-report --run-dir "$RUN_ROOT" --output-dir "$WORKSPACE_ROOT/交付/CV样本报告-唯一标识"
python "$SKILL_ROOT/scripts/run.py" verify-export --bundle-dir "$WORKSPACE_ROOT/交付/CV样本报告-唯一标识"
```

宿主没有uv时使用既有依赖工具，同环境binary-only安装；不能改装模型或偷偷切到其他环境。preflight只核对版本，真实import也在监测worker内。probe-cv默认300秒，外层执行超时留至少360秒，逐步监测；B失败先status/render-report并交付诊断，停止后续分析，不把A成功当成CV成功。

低内存配置最大边320、单线程、保留原PTS、有界帧队列；每镜头都有代表帧，上限300，超限失败。cgroup可读时80%阈值阻断/中止；额度或进程树未知明示，SIGKILL不自动标OOM。具体参数、资源限制和产物见[CV契约](references/cv-contract.md)。结果是算法候选边界，需人工核对真实镜头质量。

用户要求稳定性测试时，以同一视频/配置显式执行 --attempt-id repeat-1、repeat-2、repeat-3；各次保持独立收据和资源，不重取数。普通重复命令复用已校验成功B，不能把缓存复用算成三次测试。每平台必须返回实际含视频/代表帧/资源的完整ZIP；安装成功、本地通过都不能替代目标沙箱验收。本阶段不调用宿主Agent分析。
