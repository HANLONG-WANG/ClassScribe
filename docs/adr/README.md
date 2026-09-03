# 架构决策记录

ADR 一经接受即为后续阶段的工程约束。改变已接受决策必须新增 ADR 取代旧记录，不得
直接改写历史结论。

| ADR | 状态 | 决策 |
|---|---|---|
| [0001](0001-separate-product-surfaces.md) | 已接受 | 课堂工作台与 IBus 是共享基础设施、独立推理策略的产品形态 |
| [0002](0002-local-only-runtime.md) | 已接受 | 正式推理完全本地，联网仅限用户主动模型操作 |
| [0003](0003-evidence-preserving-text.md) | 已接受 | 时间轴优先、文本分层、短片段共识及逐词来源 |
| [0004](0004-model-selection-and-gpu-policy.md) | 已接受 | 模型可替换、本机基准排名、严格单 GPU 有序调度 |
