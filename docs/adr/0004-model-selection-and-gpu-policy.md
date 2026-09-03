# ADR-0004：可替换模型、本机排名与单 GPU 调度

- 状态：已接受
- 日期：2026-09-03
- 关联：`PRD-MODE-*`、`PRD-PRIN-004/005/008`

## 背景

公开模型指标不能代表本机课堂录音，模型依赖之间也可能冲突。目标 RTX 4070 的常见
12 GB 显存不足以安全并发多个重型推理任务，而 IBus 对延迟最敏感。

## 决策

所有模型名称、版本、能力和环境进入版本化注册表，具体模型库不进入核心环境。
`auto_best` 初次由公开事实 bootstrap，稳定值由本机课堂与听写 gold benchmark
分别校准。`manual_primary` 允许质量 fallback，`strict_single_model` 禁止 fallback。
单 GPU 默认一次只运行一个重型推理任务，IBus 优先级最高。

## 后果

业务流水线不得硬编码模型。每个模型使用独立 worker 环境。GPU 调度只由共享调度器
决定，WebUI、IBus 与 worker 不得自行绕过。
