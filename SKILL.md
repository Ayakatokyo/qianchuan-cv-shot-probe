---
name: qianchuan-cv-shot-probe
description: 用于千川视频CV沙箱样本的A阶段输入测试；通过授权取数、RPA CSV和来源校验下载一条视频，生成技术报告。当前版本不运行CV、ASR或模型分析。
metadata:
  argument_hint: 使用平台授权表单，获取1条视频并验证沙箱输入链路。
---

# 千川视频输入沙箱样本（A阶段）

当前已实现输入测试；CV切分、音频转写、脚本拆解和宿主二次分析未加入。只获取一条视频，不补位，不自动重提任务，不加载ASR/OCR/CV或看图模型。

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

export-report只选入报告、源视频（有合法下载收据时）、资源采样及bundle-manifest，不包含原CSV、账号、请求或签名URL。交付生成的ZIP文件及其SHA；不能只用报告归档器导出report子目录后称为含源视频。以实际下载ZIP文件清单为准，至少检查manifest、report、media和resources；缺项说明交付不完整。失败态报告保留已完成的素材与实测媒体，但不生成成功收据。时长按元数据精度核对：整数秒允许1秒差，小数秒默认0.1秒，宽高/FPS仍严格；比较值/容差写入报告。
