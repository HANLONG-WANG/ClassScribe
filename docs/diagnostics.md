# 本地诊断数据模型

`DiagnosticSnapshot` 是诊断页和可导出 zip 的唯一数据源：

- system：Fedora 版本、内核、桌面与 Wayland/X11；
- gpu：GPU、驱动、显存和 CUDA 状态；
- components：FFmpeg、PipeWire、IBus 与 portal 后端；
- workers/models：隔离环境、协议版本、模型 revision 和健康；
- gpu_lease/gpu_queue：当前租约与等待队列；
- recent_errors：只含结构化 error code 和脱敏上下文；
- metrics：阶段耗时、worker load/unload、OOM、重复拦截、fallback、自动采用、低置信度，
  以及 IBus p50/p95 latency。

命令探测使用无凭证的最小环境、2 秒 timeout 和截断的首行输出。导出前所有字段再次
递归脱敏，home 路径替换为 `[HOME]`；zip 以 `0600` 原子写入且永不自动上传。

IBus 设置页另外读取已认证 `GET /api/v1/ibus/status`。响应只汇合 daemon 的 revision、
IDLE/ARMING/LISTENING/INTERIM/FINALIZING/CANDIDATE/COMMITTING/ERROR 状态、非正文配置、
具体预加载模型，以及 `portal-status.json` 的 available/diagnostic。状态文件必须为当前 UID 的
普通非 symlink 文件且不超过 16 KiB；不存在或非法时返回 `portal.available=false` 和明确 fallback，
而不是让诊断 API 失败。延迟只进入 IBus p50/p95 指标，不记录 preedit/commit 正文或 PCM。
