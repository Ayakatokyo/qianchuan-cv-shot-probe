# 0.5.0当前批量与轻量报告

run-batch一次选材、1–10条不同素材、串行A+B，保留全部输入门禁、原生worker和进程清理；首败停余项pending，短缺partial，不自动补位/恢复。acquire只做单条A；probe-cv-batch已有A默认一份HTML，--delivery audit才逐条大ZIP。默认export-html无MP4复制/读取或ZIP，已完成报告快照、CV收据输入SHA、镜头及代表帧核验；完整A输入核验留verify/probe-cv。HTML单帧256KiB、总图片12MiB，超限显示对应缺口、所有镜头区间保留；一帧一帧写入，不全量加载图片。中文素材导航/时间轴/大图/画廊参考现有元技能与脚本工作台。约200ms守卫和缓存建议保留在原目录html-exports，不默认再交付ZIP。默认报告不含视频播放，完整媒体留原目录；历史技术ZIP规则仅适用于显式审计。

# 分镜与内存契约（0.4.1）

B复用A成功收据绑定的视频，先验证原CSV/视频SHA；不重提API/RPA或重新下载。成功缓存绑定实现/视频/配置/backend；显式新attempt-id用于重复测试，保留旧状态/收据。

## 默认FFmpeg原生方案

默认backend=ffmpeg-scene，只依赖requirements.txt中的A依赖与imageio-ffmpeg，不安装NumPy/OpenCV/PySceneDetect。preflight --cv仅核对安装元数据，worker再检查FFmpeg scdet滤镜；不支持该滤镜则明确失败，不能自动改用AdaptiveDetector。

FFmpeg单线程逐帧解码、最大边320、scdet默认阈值10，Python只接收时间戳和场景分数，不接收像素数组。原始PTS与帧序号保存timeline，showinfo核对解码覆盖和最后帧时长；不强制VFR转CFR。镜头最短0.2秒、连续左闭右开，每镜头取中点前最近帧，第二次串行解码一次提取全部JPEG，标准库检查JPEG结构/尺寸。镜头上限300、帧10801、超时300秒。解码损坏/缺帧/超限/代表帧失败均失败，无整段回退；无切点的合法单镜头成功。

--backend adaptive为显式对照；先binary-only安装requirements-cv.txt，固定numpy2.2.6/opencv-python-headless4.11.0.86/scenedetect0.6.7.1。两算法阈值语义不同，真实渐变/闪光/快速运动切点可能不同，应人工核对，不能宣称等价质量。

## 技能守卫与沙箱硬额度

memoryGuardFraction、memoryHardFraction及maxProcessTreeRssMiB是技能策略，sandboxLimitBytes来自环境cgroup内核额度；技能只读，不改变沙箱额度/系统缓存。

0.2.0以原始总占用80%停止，缓存较高时易提前阻断。0.3.0读取memory.stat，工作集估算=usage-保守inactive_file扣减；只计同口径file/cache范围内的inactive_file，并额外扣除shmem/dirty/writeback。v1优先total层级统计，数据缺失回退原总占用口径；不将全部cache或匿名RSS当作唯一判定，不保证扣减值立即可回收。

默认：工作集估算达到额度80%停止；原始总占用达到95%即紧急停止；本次监督器+worker+FFmpeg进程树RSS超过256MiB停止。新增failcnt/oom/max事件或高总占用同时出现full压力也停止，历史计数不触发。额度/进程RSS未知明确记录；监测每0.2秒，资源约1秒采样，不能保证拦截瞬时OOM。共享事件不能归因某worker。

memory-guard.json记录policyOrigin=skill、limitOrigin=sandbox_cgroup、原始占用/估算/扣减、阈值、原因与观察缺失；报告和完整导出含此文件。守卫主动SIGTERM记skill_guard_terminated，超时记skill_timeout_terminated；SIGKILL仍不能直接认定OOM。

## 状态与交付

A video_ready保持，B独立not_run/running/succeeded/failed/interrupted；运行锁和独立进程组清理。cv/<attempt>/含配置、监督状态、worker环境、时间线/边界、shots与全部frames、资源与SHA收据、守卫决策及失败诊断；verify-cv验证输入/文件/完整镜头覆盖。

render-report含视频、全部镜头/代表帧/跳转和失败/资源证据。export-report导出report+合法源视频+A/B资源+CV配置/状态/收据/环境/诊断/镜头/全部帧/守卫，verify-export校验清单/哈希/引用。私有CSV/URL/账号/job/原始解码日志留原目录。交付生成的完整bundle ZIP；report归档器若只导出3个report文件，不能当作含视频交付。

生意高手每平台需原A目录B单次/显式三次运行、真实视频人工切分和内存核验。本地合成视频/缓存快照回归/最小依赖验证均不替代1GiB目标沙箱验收。当前无ASR/OCR/脚本对齐/Agent二次分析。

## 0.4.0当前补充

config/memory-policy.json规定阶段启动余量，B128MiB基于原始占用距离技能95%停止线计算（1GiB额度时B需raw占用不超过约844.8MiB），不把inactive_file抵扣成保证可用容量。运行中仍沿用80%工作集估算/95%总占用/256MiB进程树/共享压力事件；额度不可读即unknown，无假余量。统计扣减字段缺失时列出missingReclaimDeductionFields，估算仍不是保证可回收容量。

A阶段不足以paused/insufficient_stage_headroom保存状态，明确授权resume后复用已绑定选择/任务。B不足仍failed/insufficient_headroom，保存memory-admission和memory-guard，无worker退出码；不要诊断为FFmpeg被OOM杀死。包版本/策略/实现哈希绑定成功缓存，旧0.3.0成功不会掩盖新版实际执行。

B验证A收据后复用已校验媒体参数；下载时流式计算SHA生成下载收据，恢复、A成功报告、B输入/worker及完成校验仍保留独立内容校验。资源采样增加Mac ps进程树RSS；全过程峰值由resources.ndjson及B guard-samples.ndjson聚合，HWM与采样RSS口径有别，RSS求和可能重复共享页，采样可能漏峰。

对当前运行目录acquisition/media内完成的普通文件执行fsync和POSIX_FADV_DONTNEED建议。Linux支持不等于一定回收；Mac不支持则记录supported=false，失败记录errno，不修改系统缓存/额度、不删除文件。cache-advice的前后cgroup差值不归因单一文件或技能。报告完整包新增phase-memory、cache-advice、memory-admission和guard-samples诊断；不包含原CSV/账号/签名URL。临时导出副本的缓存回收尚未覆盖，导出32MiB仅为试验准入预算。

0.4.0本机回归不能代替目标Linux1GiB沙箱验收；下一步测两平台A+B三次、缓存建议效果、峰值与人工镜头质量。宿主音频转写、脚本对齐与二次分析后置。

## 0.4.1当前契约

0.4.0只记录missingReclaimDeductionFields但仍默认缺值为0的问题已修正：只有inactive_file、file/cache及同层级全部shmem/dirty/writeback字段可读时才抵扣；否则inactiveFileCreditBytes=0、guardBasis=raw_usage_fallback。v1 total统计不混用local扣减字段。阈值/余量保持不变，缺失字段的高raw基线会比旧版本更早被保护阻断。

导出独立`.memory`目录约200ms采样和守卫锁存，覆盖render/copy/hash/verify/ZIP/cache advice/publish；1MiB复制/ZIP块和阶段边界合作停止，短峰值仍可能漏采。调用只建议回收本次显式普通副本（含JPEG）与已关闭、CRC/清单/SHA核验后的ZIP，fsync+DONTNEED失败记录errno，保留交付文件、不扫描父目录/跟随内部软链接/清全局缓存。导出后独立`.memory.zip`含resources.ndjson、guard-samples.ndjson、memory-guard.json、cache-advice.ndjson、status.json、receipt.json。receipt绑定报告ZIP SHA、观察峰值、raw基线和各证据文件SHA；证据ZIP自身的组装不在监测范围。outputPublished保留发布后最终采样失败的实际状态，失败不能称成功。

probe-cv-batch --manifest-file --output-dir为已有A输入串行入口。清单严格schemaVersion/runs、每条只有绝对runDir，清单≤64KiB、1–10项，纯本地平台/请求契约预检。输出新目录且不与输入重叠。每条调用原probe-cv（显式新attempt/native）与verify-cv、等待进程组清理，再export-report；concurrency=1，不取数/重下视频，不读全部帧到内存。batch.json逐条落盘，首败停队列，余项pending；无自动恢复。整队列独立资源/守卫包含两条间隔与导出，避免只看B峰值。distinctVideoCount统计实际执行收据的视频哈希，重复一个素材不等于多素材证据。A的单条样本门禁、账号/周期/身份/恢复绑定保持；0.5.0新增run-batch正式一次选材后串行详情/下载，默认HTML，以上当前节为准。

串行队列导出在运行锁内核对expected attempt；期间latest被其他已完成任务替换则delivery_cv_attempt_changed停止，不能将另一attempt的报告混入当前条收据。failcnt表示触限计数，不等于OOM次数；PSI字段缺失时压力未知。
