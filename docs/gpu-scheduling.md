# 单 GPU 调度与显存恢复

`GPULeaseManager` 是 core 唯一 GPU 所有权仲裁器。优先级固定为：IBus 录音／确认 `0`、
WebUI 交互 `10`、课堂主转写 `20`、备用复核 `30`、后台 benchmark `40`。同优先级 FIFO，
任意时刻只有一个 lease owner。

IBus 开始时立即停止派发新的优先级 20+ 工作；已运行课堂片段不会中途截断，只在调用
`segment_boundary()` 后让出 lease。IBus 结束后队列继续。resident model ID 随 lease 保留，
相同模型复用；模型不同或显存预检失败时要求卸载。

生产运行同时交接真实 CUDA 进程：课堂 `SandboxedModelInvoker` 在每次允许启动 worker 前同步
要求 core supervisor 卸载全部 IBus resident process；IBus begin 先设置全局 dispatch block、等待
数据库中所有 RUNNING job 到 checkpoint 变为 paused，再加载预算内 resident ASR/VAD/LID 并回复
lease。IBus end 先 unload resident，再解除 block，并只恢复由本次抢占暂停/延后的 job。安装或
profile 刷新在活动听写期间延后到 idle，不替换当前会话中的 socket。

`ResidencyPlanner` 不含模型名或语言分支，只查询 registry profile 和 capability：fast 只留
一个小流式模型；balanced 优先一个兼具 streaming/final-decode 的模型；accuracy 在两个
模型估计显存加最大安全余量不超过实机总量时双驻留，否则明确 hot-switch。
热切换只能由持有 priority-0 session 的 dictationd 通过 same-UID lease socket 请求，并必须提供
已由主 ASR/FireRedLID 解析出的具体语言。core supervisor 保留 CPU VAD/LID、卸载全部流式 GPU
进程、加载该语言 accuracy ranking 的首个已安装固定 revision，再发布新 socket；batch-only
最终模型通过 worker 内临时 WAV 的 `transcribe_pcm` 桥执行，确认结束后与其余 resident 一并卸载。

`NVMLBackend` 通过系统 `libnvidia-ml.so.1` 读取 used/free/total，不向 core 引入 PyTorch、
CUDA Python 或具体模型依赖；不可用时安全返回 no-sample。monitor 保存当前、峰值和每次
OOM 的 request/model/参数指纹/显存样本。

OOM 恢复严格有限且每次参数指纹必须变化：清退出 worker/CUDA cache → batch=1 → 窗口
减半（不低于安全最小值）→ SDPA 低内存 attention → 已验证 BF16/FP16 或量化 → registry
中更小候选 → 记录失败。没有已验证的 dtype/量化／小模型时跳过该变化，绝不重复同参数；
耗尽后抛出 `OOMRecoveryExhausted`，上层只失败当前片段。多课堂候选由
`run_candidates_sequentially` 严格按 registry 顺序逐一完成 load/infer/unload callback，
不并行驻留。
