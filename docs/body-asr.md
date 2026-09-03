# 三语正文 ASR

## 边界与候选顺序

正文识别只消费阶段 6 产生的 `TranscriptChunk`。请求窗口可含上下文重叠，唯一正文归属始终
是 `core_start_sample/core_end_sample`；每个候选都绑定同一自然句段，不能把整段长文本压到一个
时间点。结构层 MOSS 粗文本可作为后续共识参考，但 `dispatch_body_asr=false`，不会冒充正文
模型输出。

| 手动语言 | 普通候选顺序 | 额外显式候选 |
|---|---|---|
| 中文 `zh` | FireRedASR2-AED → ARK → Qwen3-ASR 1.7B → MOSS 结构参考 | 无 |
| 日语 `ja` | Granite Speech 4.1 2B → Qwen3-ASR 1.7B → ARK → MOSS 结构参考 | Qwen 强制日语实验 flavor、Fun-ASR 专家候选 |
| 英语 `en` | MOSS Transcribe Preview 2B → ARK → Granite → Qwen3-ASR 1.7B | 术语密集时阶段 8 可提升 Granite |

顺序来自 `config/model-registry.v1.yaml`，业务代码不按语言硬编码 repository 或 revision。
`BodyASRPlanner` 只把 registry 项映射为 primary/secondary/tertiary/structure/expert 角色。
当前没有上游发布的独立 `Qwen3-ASR-1.7B-JA` checkpoint；该实验候选被准确实现为同一固定
1.7B checkpoint 的显式 forced-Japanese 路由，不虚构第二个模型 ID 或 revision。

## 请求与解码合同

`build_asr_request` 对每个自然句段生成一个 `body-asr-v1` 请求，并同时执行 registry 和运行时
语言校验。正文语言必须由用户模式明确为 `zh/ja/en`；不接受 `auto`，FireRed 在 core 和 worker
两层都拒绝日语。

所有生成式路径固定：

- `temperature=0.0`、`do_sample=false`、记录非负 seed；
- `batch_items=1`、`batch_size=1`、`mixed_length_batch=false`；
- `max_new_tokens = clamp(ceil(seconds × 16) + 32, 64, 2048)`；
- `max_output_characters = max(128, ceil(seconds × 40))`；
- 只读取 16 kHz、单声道、signed 16-bit canonical WAV，并物理裁出请求区间再交给模型；
- 后端文本或 generated-token 计数越界时，候选在持久化前整份拒绝。

共享 `classscribe_protocol.batch_audio` 在 worker 内复验这些条件，因此直接绕过 core 构造器也
不能开启随机采样、异长混批或多音频批次。每个 worker 只从完整、非 symlink 本地 snapshot
加载并设置 local-only/offline 参数。

## 模型族行为

- FireRedASR2-AED 使用上游 `FireRedAsr2Config(return_timestamp=True)`，保存未经跨模型校准的
  句段置信度和原生字/词时间；局部秒数转换为请求窗口内的绝对 sample。中英混说的 ASCII
  文本不做中文化。
- ARK 使用官方 processor/chat 格式、SDPA 和 control-token mask。提示明确要求以所选语言
  抄录而不翻译；registry 声明它没有 hotword 能力，因此不把术语塞入未支持的通道。
- Qwen 使用固定 commit 的官方 `qwen-asr` Transformers backend，`max_inference_batch_size=1`，
  并把语言明确传为 `Chinese/Japanese/English`。canonical+reading 只进入带“不凭列表插入”约束
  的 context。
- Granite 使用官方英文任务提示 `transcribe the speech to text.`；日语也不改用非官方本地化
  提示。支持的关键词通道附上 `canonical (reading)`。
- MOSS Preview 使用 checkpoint 自带的本地 dynamic model/processor、128-bin mel 配置和 chat
  template，固定 greedy decode。模型没有受支持的术语通道，提示会记录 warning 而不伪造 API。
- Fun-ASR 使用上游固定 Transformers commit，只允许 `course_expert` 且
  `experimental_enabled=true`；registry 默认关闭。每次输出必须先通过四连 token n-gram 循环
  检查，失败不返回候选。阶段 8 还会执行统一的更严格候选 QA。

## 读音提示与文本变体

`PronunciationHint` 保存标准写法、读音、语言和以下类型之一：课程术语、人物、地名、组织、
植物、药物、公式读法。只有匹配当前手动语言的提示进入请求；提示仅是发音/术语 bias，不具有
新增正文的权限。阶段 8 的最终 token 仍须有声学候选来源。

中文繁体不是新一次 ASR。`TraditionalChineseConverter` 仅从忠实简体层执行 OpenCC `s2t`，
返回带 `source_layer=faithful_simplified` 和 `transformation=opencc_s2t` 的独立显示/导出变体；
若转换改动嵌入的 ASCII/英文序列则立即失败。

## 候选证据与持久化

`ASRCandidateEvidence` 包含 model ID、40 字符 revision、角色、请求/报告语言、上下文和 core
绝对区间、raw/normalized text、原始置信度、原生 token 时间、完整 decode、推理指标、warnings
和 provenance。`parse_asr_response` 复验模型身份、绝对范围、单调 token、字符/token 上限和有限
置信度。

`ASRCandidateRepository` 先幂等建立自然 `transcript_segment`，再以短事务追加不可变候选和
`token_spans`。新一次同模型运行通过 `supersedes_candidate_id` 保留版本链；正文候选初始均为
`is_adopted=false`、`confidence_calibrated=null`，阶段 7 不以 raw confidence 跨模型做决定。
原生 token 时间写入 `native_model_time=true` provenance；无原生时间的生成式候选只保存准确的
请求区间，并等待阶段 8/9 的受约束对齐。

## 验证

`tests/integration/test_body_asr_workers.py` 用 espeak-ng+FFmpeg 生成真实中文、日语、英语语音 WAV，
贯穿生产适配器的本地 snapshot load、精确裁剪、请求和响应路径；测试替身只代替不随仓库分发
的模型权重。测试同时覆盖所有六个 worker family、英文嵌入保真、原生绝对词时、官方提示、
强制语言、固定 seed/greedy/token 上限、单批合同、专家门控和循环拒绝。实际固定权重的 GPU
准确率、RTF、VRAM 和课程 gold 排名属于阶段 12 benchmark，不用 bootstrap 事实冒充本机结果。
