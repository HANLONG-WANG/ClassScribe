# 故障恢复策略

## 批处理

1. 每个 job 由有序 checkpoint 描述；segment checkpoint 保存参数 SHA-256、attempt 和
   每个参数组合的 `max_attempts`。
2. 处理前单独提交 `running` 与 attempt。输出和 `completed` 在一个短事务中提交。
3. worker 崩溃回滚当前片段输出，只把当前 checkpoint 置为 retryable/failed；已完成
   片段不会回滚。
4. core 重启扫描 pending/running/cancelling job。遗留 running 从第一个未完成 checkpoint
   恢复；cancelling 安全收敛到 cancelled；paused 不会擅自恢复。
5. pause、resume、cancel 和 retry 只允许显式合法转换，重入 complete/cancel 不重复工作。
6. MOSS 结构完成后按当前活动 segment 幂等创建逐片段 checkpoint；拆分、合并或局部重试只
   重排受影响 segment。SSE event ID 可续传界面状态，但恢复决策只读取 SQLite。

最终导出使用同目录临时文件、flush/fsync、`os.replace` 和目录 fsync，消费者只能看到
旧的完整版本或新的完整版本。异常会清除临时文件。

## IBus 安全回退接口

| 故障 | 固定动作 |
|---|---|
| dictationd 不可用 | 取消当前 preedit、不提交，普通按键原样交给应用并显示错误 |
| 麦克风断开/GStreamer EOS | 停止 source、abort worker、清空内存 PCM/preedit、释放租约，不提交 |
| GPU OOM/worker 错误 | abort/close 当前 stream、清空候选与 preedit、释放租约并显示结构化错误；不在同一会话静默换文 |
| worker/control/lease socket 断开 | 有界超时，取消半句，绝不静默 commit |
| 音频队列溢出 | 立即失败并丢弃整段，而不是跳帧后提交错时文本 |
| Portal 拒绝/超时/快捷键冲突 | 写出本地诊断，保留专用 IBus 输入源，不使用 X11 grab |

听写历史默认不创建数据库记录；commit/cancel/error 后清除内存 PCM。FireRedLID 的 file-only
临时 WAV 使用作用域临时目录并总是删除。当前版本没有隐式 opt-in 保存路径，因此原始麦克风音频
不会持久化。

## 时间轴与对齐

- 任一 token span 非单调、越出 segment 或无效时，整份候选设为 invalid 并写审计事件。
- forced alignment 失败时保留结构层粗时间并记录 `alignment_fallback`，不得伪造精细词时间。
- 相邻窗口无法确定去重时保留全部候选并记录冲突，不静默丢内容。

## 故障注入证据

`tests/unit/test_job_state_machine.py` 模拟进程重启、worker 异常、重试耗尽和取消；
`tests/unit/test_recovery.py` 覆盖原子写、四类 IBus 故障和时间轴异常；数据库、并发编辑、
删除和脱敏分别在 integration/unit 测试中使用临时 XDG 根及文件型 SQLite 验证。
`tests/unit/test_classroom_pipeline.py` 另覆盖多片段唯一 checkpoint、重启恢复、失败后局部重试、
自动导出最后执行，以及 IBus 请求只在安全边界暂停。
