# 本机 Gold benchmark、校准与自动选模

## 证据边界

权威 gold 位于 `${XDG_DATA_HOME}/classscribe/benchmarks/<dataset>/`，不把私人课堂录音、近讲
听写或人工校正文稿提交到源码仓。仓库只保存 schema、normalizer、scorer、runner、无敏感信息的
格式示例和固定算法回归。公开榜单与 `config/model-registry.v1.yaml` 的 bootstrap 顺序不是本机
准确率证据；只有通过本文全部门禁的已安装固定 revision 才能成为稳定 `auto_best`。

runner 强制 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 和 `HF_DATASETS_OFFLINE=1`。模型、音频、
人工文本、参数或校准缺失时明确失败，不下载、不以合成样本补足、不把未运行项目记为通过。

## Gold JSONL 与覆盖门

每行遵守 `config/schema/benchmark-gold-item.v1.schema.json`，使用 canonical 16 kHz 绝对 sample：

- 身份：`item_id`、相对且不可 traversal 的 `audio`、`scenario=classroom|ibus`、
  `split=train|validation|test`、`language=zh|ja|en`；
- 标注：人工 `text`、speaker、word range、sentence boundary、speaker/voiced spans；
- 课程证据：本句 `terms`、全课程 `term_vocabulary`，以及 number/unit/negation/name entities；
- 场景标签：清晰、远场、学生提问、术语、数字/专名、code-switch、overlap、silence、
  background music、interrupted 和连续超过 60 秒。

同一录音或说话人不得跨 train/validation/test。生产覆盖门对中、日、英分别要求：

- 课堂音频累计至少 30 分钟（早期数据建设可用 `--starter-coverage` 验 5 分钟，但不能发布排名）；
- 至少 50 条 IBus 近讲听写，且至少一条真实连续输入超过 60 秒；
- 三个 split 均非空。推荐继续扩至每语言 100 条 IBus，并覆盖 1、2、5+ 说话人。

loader 拒绝重复 ID、空清单、绝对/traversal/任一级 symlink/缺失音频、非 PCM 16 kHz mono
S16LE WAV、超过真实帧数的 sample、乱序词/句边界、非法语言/场景/split 和超过 128 MiB 的 JSONL。

## 预测合同与统一调用

每个候选模型通过同一隔离 worker 边界产生 `benchmark-prediction.v1` JSONL：模型 ID、不可变
revision、文本/词时/句边界/speaker/voiced spans、raw confidence 与固定质量特征，以及 runtime、
load、VRAM、RAM、IBus interim/commit 和抢占恢复延迟。`BenchmarkRunner.execute()` 对每个明确的
`(model_id, revision)` 调用同一个 async provider；返回身份不符立即失败。语言专用模型只需覆盖其
支持的语言/场景，但一旦在某组出现，必须完整覆盖该组所有 gold，不允许挑容易样本。

离线评分命令：

```bash
uv run python scripts/run_benchmark.py \
  --manifest "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/gold.jsonl" \
  --manifest-version course-v1 \
  --predictions "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/predictions.jsonl" \
  --parameters-json "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/parameters.json" \
  --output "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/report.json"
```

报告目录和文件分别为 0700/0600，原子替换；记录 manifest SHA-256、硬件、decode 参数、模型
revision、逐项 gold/prediction/metrics、校准工件和分语言/场景排名。`BenchmarkRepository` 用一个
短事务写入 `benchmark_runs/items`，失败 run 不可用于 profile。

生产预测必须同时记录 `provenance_coverage=1` 和非零 runtime。IBus test split 的每条预测必须
都有 interim/commit 延迟；runner 在完整 test split 上重新计算 p50/p95，而不是平均逐条 percentile。
API 只接受带 manifest SHA-256、`production_gold=true`、`real_model_execution=true` 且
`synthetic_gold=false` 的完成 run 覆盖自动最佳，starter 或 fixture 报告永远不能应用。

## Normalizer 与指标

`benchmarks/normalizers/policy.v1.json` 是可比较性合同：

- 中文/日语 raw CER 使用不含空白的原 Unicode 字符（含标点）；normalized CER 使用 NFKC、
  embedded Latin casefold、去空白/Unicode 标点并保留数字；
- 英语 raw WER 使用 NFKC 后保留大小写的可读词；normalized WER 使用 lowercase、无标点 word，
  并统一弯/直撇号；
- 报告始终同时保存 raw 与 normalized policy 名称，禁止跨 policy 比数值。

`metric-contract.v1.json` 固定以下结果：

- 正文：中文/日语 raw+normalized CER，英语 raw+normalized WER；
- 课程：术语 P/R/F1，数字、单位、否定和姓名 recall/accuracy（无该类 gold 时为 `null`）；
- 格式：按 lexical anchor 的标点 P/R/F1、PER、英语 capitalization F1、未配对引号/括号数；
- 安全：漏句率、静音幻觉/小时、循环触发/小时、有声覆盖率、异常字符率；
- 时间/说话人：非法/倒序/重叠/越界数、词边界 MAE、250/500 ms 句边界 F1、DER、JER、
  speaker-attributed CER/WER 等价错误率和跨窗 speaker switch；
- 性能：RTF、实际总耗时和按 RTF 投影的 90 分钟耗时、峰值 VRAM/RAM、load、preemption resume、
  IBus interim/commit p50/p95。

## 校准、排名与回滚

每个 model revision × language × scenario 单独校准。raw confidence 与质量特征只用 train 拟合；
runner 同时拟合 isotonic PAV 和 Platt scaling，以 validation Brier score 选择；test 只用于最终报告。
校准工件含错误阈值、参数、feature count、train hash、manifest hash、model revision、validation/test
Brier 和生成时间。revision 或 manifest hash 改变时 `valid_for()` 必然失败；registry upgrade 同时
清空旧 benchmark 元数据。

排名先执行硬约束，再计算综合分：正文错误 + 术语惩罚 + 漏句/静音幻觉/循环高额惩罚 + 时间/
DER/JER + 标点 + RTF/VRAM。任一时间结构错误、循环、静音幻觉、过慢课堂 RTF，或 IBus 超过
interim p50 600 ms、commit p50 1.2 s / p95 2.5 s 均不进入可应用列表。缺失/过期校准同样失格。
课堂/IBus 和中/日/英独立排名，不混合 normalizer。

自动 QA 流水线还必须相对本机最优单模型满足：主要 CER/WER 绝对恶化不超过本机配置阈值
（默认工程门 0.03）、术语 F1 不下降、漏句/幻觉不增加、循环为零、最终 token provenance
coverage 为 1。具体准确率目标必须由用户 gold 填写，源码不虚构。

只有 status=`completed` 且 `passed=true` 的精确排名才能由 API 应用；请求模型顺序必须与报告完全
一致。Profile 每次更新保存前一模型列表与 run ID，可经 rollback 恢复；registry ranking 也保留
原子历史。模型 revision 更新后安装、回归、校准、排名必须全部重跑。

## 固定回归与桌面矩阵

`tests/golden/regressions.v1.json` 固定六类已知失败：长结果只取第一块、Fun-ASR 无限循环、无标点/
`??`、三模型同一时间点、错误文字被强制对齐、VAD hard cut 无 overlap。每项绑定具体自动测试；
这些是 guard evidence，不冒充用户准确率 gold。

原生 IBus 矩阵使用：

```bash
uv run python scripts/validate_ibus_desktop.py --output \
  benchmarks/results/ibus-desktop-compatibility.json
```

inventory 只识别已安装程序，仍记为 `not_run`。人工在 Wayland GNOME/KDE、X11 GNOME/KDE 对 GTK、
Qt、Firefox、Chromium、Electron、终端和 LibreOffice 完成 preedit、plain fallback、lookup、单次
commit、cancel、错误透传后，才可提供 evidence JSON 并用 `--require-complete` 签核。当前仓库报告
如实保留未安装和未执行项。

最终 Phase 12 门使用 `benchmarks/manifests/test-evidence.template.json`。十类证据不能写成简单
布尔值：每项必须包含实际命令、完成时间和证据文件 SHA-256；真实模型/GPU 项还必须标为
`real_model` 或 `real_hardware`。Worker 项严格按 `config/worker-contract-matrix.v1.json` 覆盖全部
11 个 worker 和十种 case，逐项保存固定 model revision、退出前后 VRAM，退出后相对 baseline
残留超过 128 MiB 即失败。`scripts/validate_phase12_acceptance.py` 只在 gold、benchmark、桌面矩阵
和这些结构化证据同时完整时返回 0。
