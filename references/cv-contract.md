# 分镜与内存契约（0.3.0）

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
