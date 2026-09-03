# ClassScribe 首版验收清单

本清单把阶段 0 的成功合同投射到后续阶段门。勾选必须基于右侧证据，不得以“代码
大致写完”替代。完整逐项映射见 `requirements-traceability.md`。

## 每阶段通用门

- [ ] 本阶段的全部交付物存在，且没有未登记的阻塞项。
- [ ] 本阶段涉及的需求编号已在追踪矩阵中链接到实现和有效验证。
- [ ] 自动测试在默认断网环境通过；测试未隐式下载模型或上传数据。
- [ ] 新增文本采用能够追溯到模型候选或确定性规则的来源结构。
- [ ] 新增时间字段建立在 canonical timeline 上，没有第二套持久化时间轴。
- [ ] WebUI、IBus、worker 没有复制核心配置、模型选择、日志或 GPU 调度逻辑。
- [ ] README 与 UI 没有出现被禁止的准确率承诺。

## 产品级最终验收

### 课堂转录

- [ ] 90 分钟音视频可导入、断点续跑，并在核心服务重启后恢复（`PRD-CLS-001`、
  `PRD-QUAL-002`）。
- [ ] 四种语言模式均能无人干预地走完时间轴、说话人、正文、标点、术语纠正和
  导出（`PRD-CLS-002`、`PRD-CLS-004`、`PRD-QUAL-001/003`）。
- [ ] 1～2 人及多人样例保留说话人标签，重叠语音被标记，标签可改名且保留审计
  关系（`PRD-CLS-003`、`PRD-SPK-001`）。
- [ ] 跳过校对页面仍能导出，进入校对页面可比较三层文本和来源（`PRD-CLS-005`、
  `PRD-TEXT-001/002`）。

### IBus

- [ ] Fedora/IBus 中可启用 `ClassScribe Voice`，Wayland 全局快捷键可触发按住说话、
  预编辑和松开提交（`PRD-IBUS-001/003/004`）。
- [ ] 四种语言模式均通过短听写和超窗口连续听写（`PRD-IBUS-002/005`）。
- [ ] 长听写边界的词级基准证明没有重复或漏字（`PRD-QUAL-008`）。
- [ ] IBus 抢占／优先于课堂重型 GPU 工作，且薄引擎不加载模型（`PRD-PRIN-005`）。

### 质量与可追溯性

- [ ] 时间戳单调、位于音频范围内，sample↔显示时间换算无累计漂移
  （`PRD-QUAL-004`、`PRD-PRIN-001`）。
- [ ] 循环、静音幻觉、脚本、异常字符和时间轴故障夹具全部被质量门拦截
  （`PRD-QUAL-006`）。
- [ ] 共识只比较已对齐短片段；不存在长字符串投票路径（`PRD-QUAL-005`、
  `PRD-PRIN-002/003`）。
- [ ] 导出的每个自动词可回溯模型／版本／音频区间，或确定性规则及差异
  （`PRD-QUAL-007`、`PRD-PRIN-006/007`）。
- [ ] 三种听不清标记格式正确并引用真实区间；低质量内容不会被猜测补齐
  （`PRD-TEXT-003/004`）。

### 模式语义与模型选择

- [ ] 课堂和 IBus 是不同产品模式，共享合同但允许独立模型配置
  （`PRD-ARCH-001/002`）。
- [ ] 语言枚举只有 `zh`、`ja`、`en`、`auto_mixed`（`PRD-MODE-001`）。
- [ ] `manual_primary` 可触发质量 fallback；`strict_single_model` 从不触发
  fallback（`PRD-MODE-002/003/004`）。
- [ ] 中文简体／中英混说与独立繁体转换通过样例（`PRD-MODE-005`）。
- [ ] 自动最佳从 bootstrap 迁移到本机 gold benchmark 排名（`PRD-MODE-006`、
  `PRD-PRIN-008`）。

### 隐私与对外承诺

- [ ] 在阻断外网的完整运行时验收中，无 DNS／HTTP 请求且所有功能可完成；只有用户
  主动模型操作具有隔离联网入口（`PRD-PRIV-001/002/003`）。
- [ ] 日志、诊断包和导出没有意外暴露原始内容；所有数据留在受限本机目录。
- [ ] README、WebUI、IBus、CLI、RPM 描述和帮助文档均通过禁用措辞扫描
  （`PRD-NONGOAL-001/002/003`）。
- [ ] 忠实层不可被生成式自由改写路径覆盖（`PRD-NONGOAL-004`）。

## 2026-09-04 当前签核记录

工程侧清单、静态门、自动测试、RPM 重定位生命周期和 fail-closed validator 已执行；机器结果中
`artifact_inventory`、`model_revision_lock`、`model_license_inventory`、`dependency_lock_hashes`、
`final_architecture`、`production_runtime_binding` 为通过。上面的产品级复选框仍保持未勾选，因为
`source_license`、真实模型/私有 gold 的 `phase12_acceptance` 和完整 `desktop_matrix` 尚未通过。
不得用 fixture、bootstrap 排名、未安装应用或本文件中的文字替代这些外部验收。
