# ADR-0001：分离课堂工作台与 IBus 产品形态

- 状态：已接受
- 日期：2026-09-03
- 关联：`PRD-CLS-*`、`PRD-IBUS-*`、`PRD-ARCH-*`

## 背景

课堂文件转录面向最长约 90 分钟的可恢复批处理和最高质量导出；IBus 面向低延迟
预编辑、优先抢占和持续语音分块。二者需要共享协议与数据不变量，但延迟、模型和
生命周期不同。

## 决策

把 `classroom_workbench` 与 `ibus_dictation` 固定为独立产品形态。共享 canonical
timeline、配置 schema、模型注册表、worker 协议、隐私策略、日志语义和 GPU 调度器，
但分别维护推理策略与模型选择。IBus engine 必须保持为不加载模型的薄客户端。

## 后果

后续 API、配置、数据库和界面必须携带明确产品形态。禁止创建无法判断来源的通用
“transcription mode”，也禁止 WebUI 或 IBus 各自复制共享不变量。
