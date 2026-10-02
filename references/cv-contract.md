# B阶段低内存CV契约（0.2.0）

仅复用A成功收据绑定的本地MP4，先验证CSV/视频SHA；B不调用API/RPA，不重新下载、不改动A门禁。参数、实现SHA及视频SHA绑定；同参数/实现/输入已有成功尝试时校验后复用。显式新attempt-id用于重复稳定性测试，不能覆盖旧尝试。

## 依赖与计算

Python3.12为当前验证环境。requirements-cv.txt包含A依赖，固定numpy2.2.6、opencv-python-headless4.11.0.86、scenedetect0.6.7.1。preflight --cv只核对安装版本，不导入CV；真实导入在受监督worker内。用同一Python环境binary-only安装，缺wheel或安装失败则记录，不转源码编译/模型替代。

FFmpeg单线程逐帧解码、最大边320像素；原始PTS由showinfo流式读取，队列8条、时间映射环64条，不存整段帧/全量StatsManager。AdaptiveDetector窗口2、adaptive_threshold3、min_content_val15；最短镜头0.2秒。保留原PTS，不将VFR强制转CFR；时间戳非递增、缺帧、解码失败、时长覆盖不足都失败，不伪造全视频单镜头。合法无切点视频可产生一个镜头。

镜头区间左闭右开，连续覆盖已验证视频；每镜头选择中点之前最近一帧，记录实际时间。第二次串行解码一次选出全部代表帧，不逐镜头反复解码；每镜头都有JPEG。上限300镜头/10801解码帧，超限保存边界诊断并失败，不截前12张冒充完整分镜。帧率仍受A输入门禁约束。

## 监督、状态与内存

默认300秒覆盖预检/导入/解码/代表帧；父进程清理本次独立进程组。A状态video_ready保持，B单独not_run/running/succeeded/failed/interrupted。运行锁避免并发；异常退出保留attempt，状态查询可识别中断，不生成虚假成功收据。

读取当前Linux cgroup v1/v2：可读有限额度时，启动前已用达到80%则insufficient_headroom，运行中超过80%则memory_guard_aborted并清理自己的worker；每0.2秒检查。额度未知则明确记录未知，继续受监督运行，不能宣称满足1GiB。轮询不能保证拦截瞬时峰值；共享cgroup用量/事件变化不能归因到当前worker。SIGKILL不自动等于OOM。resources.ndjson记录约1秒RSS/进程树采样，可能遗漏瞬时峰值；HWM/历史cgroup peak分别标注。

## 输出和交付

cv/<attempt-id>/含config、status、process、worker-environment、逐帧timeline、边界、shots、全部frames、resources、receipt或失败诊断；latest只指最新尝试。成功收据绑定所有尝试文件SHA，verify-cv检查输入、文件、镜头全覆盖和每镜头代表帧。原始解码日志与job保持私有。

render-report包含视频、镜头表、代表帧、点击跳转、版本/资源/失败信息；export-report导出report+合法视频+A资源+B状态/收据/依赖/诊断/配置/镜头/代表帧/资源，verify-export校验清单、哈希、HTML资源及每镜头代表帧。原CSV、URL、请求、账号、job不导出。

本阶段仅技术分镜测试；不含ASR/OCR、音频转写、脚本语义对齐或宿主AI二次分析。目标沙箱每平台需实际运行B和三次同视频重复测试，返回完整ZIP、B状态/收据/日志；本地合成视频、Linux CI或wheel下载都不能代替生意高手1GiB沙箱验收。镜头质量对真实硬切/转场/渐变/快速运动仍需人工核对。
