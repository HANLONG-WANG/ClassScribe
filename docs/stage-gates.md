# 实现依赖与阶段门

| 门 | 冻结输出 | 才允许开始的消费者 | 自动证据 |
|---|---|---|---|
| G0 产品合同 | 产品形态、模式、隐私、质量和非承诺 | 所有代码与 UI | `tests/contracts/test_stage0_product_contract.py` |
| G1 协议／时间轴／配置 | v1 envelope、16 kHz int64、XDG、config v1 | 数据库、音频、worker 实现 | 阶段 1 unit/contract/architecture tests |
| G2 数据与状态 | schema、状态机、审计、安全恢复 | 作业与推理流水线 | 阶段 2 migration/recovery/security tests |
| G3 离线安装 | 固定模型工件、RPM/systemd 边界 | 模型加载 | 阶段 3 offline/install tests |
| G4 音频基础 | master WAV、QC、VAD、LID、绝对切片 | 所有 ASR 和结构处理 | 阶段 4 audio property/gold tests |
| G5 模型执行 | 注册表、RPC、单 GPU 调度 | 结构／正文模型 | 阶段 5 contract/scheduler tests |
| G6～G9 转录核心 | 结构、三语、质量、文本层、导出 | WebUI 和 IBus | 各阶段 gold/integration tests |
| G10 课堂产品 | 版本化 API 和无人值守课堂 E2E | 桌面最终集成 | Web/Playwright/E2E |
| G11 IBus 产品 | 分块、去重、portal、输入源 | 完整基准 | Fedora 实机与边界 gold tests |
| G12 校准 | gold 排名、阈值、性能门 | 发布 | 离线全套回归 |
| G13 发布 | RPM、文档、风险闭环 | 最终交付 | clean-machine install/upgrade audit |

并行分支只能消费已冻结门的接口。任何基础 schema 改动必须先更新契约测试与追踪矩阵，
再更新消费者；不允许以临时前端类型、worker 私有时间字段或重复配置绕过上游门。
