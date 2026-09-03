# 质量门控、自动复核与时间对齐共识

## 证据边界

阶段 8 只处理同一自然正文段的 `ASRCandidateEvidence`。所有候选，包括已被质量门拒绝的
候选，都必须绑定完全相同的 canonical `audio_span` 和手动 `zh/ja/en` 语言。质量门不修改
模型文本；共识层不会调用自由生成模型补写。

`confidence_raw`、token logprob、CTC/RNNT posterior 保留为模型内证据。在阶段 12 产生本机
校准器之前，它们不会被当作跨模型概率。`quality_gate_score` 和
`consensus_support_score` 都显式记录 `score_is_calibrated_probability=false`。若没有本机校准
reliability，confusion network 对所有模型使用中性 0.5，不伪造排名。

## 四类质量特征

`QualityFeatureExtractor` 产生四个可 JSON 持久的特征域：

- 声学/模型：raw confidence、token raw confidence/logprob、CTC/RNNT posterior、
  no-speech、VAD speech ratio、SNR、clipping、RMS 和远场标记。
- 文本：有声秒的字符/词速、3～10 token n-gram 最大重复、最短循环周期、重复句、
  zlib 压缩率、`�`、连续 `??`、控制字符、脚本匹配、标点密度/句尾/成对、数字单位、
  英语大小写比例和独立标点序列。
- 时间：原生词时数量、单调/越界、相邻大面积重叠/倒序、有声区覆盖率、后半段
  仍有语音但词时已终止，以及文本长度与有声时长失配。
- 多模型：与 MOSS 结构文本的 token 编辑距离、术语疑错；候选之间另计算字/词
  编辑距离、读音/音素距离、专名出现位置和“有内容/静音”分歧。

中文比较汉字层与课程词典提供的拼音 reading；日语比较原文、reading 和片假名→
平假名归一层；英语比较 case-folded word 和可选音素。英语大小写与标点不混入
词编辑距离。实现使用有界 Levenshtein/DP；`backend/classscribe/consensus` 被架构门禁止引入
长段 `SequenceMatcher`、“选最长”或“选居中字符串”共识。

## 循环和幻觉硬拒绝

以下问题在进入共识前设置 `valid_for_consensus=false`：静音长文本、3～10 token n-gram 超过
3 次、至少 4 轮的最短循环周期、超过 24 字符/有声秒、连续 decode prefix 无新信息、
替换/非法字符、手动语言脚本明显不匹配以及非法词时。固定回归用例为
`あなたはだれですか` 无限重复。

循环拒绝产生 `RetryDirective`，将原 canonical 区间分成连续、更短且无缺口的两个请求，
强制切换到另一模型。`merge_retry_pieces` 只接受与 directive 完全一致的分片顺序，合并后
必须恢复原区间全覆盖；复核 pipeline 会拒绝没有换模型或丢失任何分片的返回。

## 条件复核

第二模型仅在任一条件出现时运行：未校准质量门分低于配置的 0.82、重复/幻觉、脚本异常、
时间覆盖不足、术语疑错、与 MOSS 分歧、低 SNR 或远场。第三模型仅在两候选的
数字/单位/否定/姓名/课程词冲突、两者均低于配置的 0.62、同音异写或内容/静音分歧时运行。
`QualityReviewPipeline` 的正常片段回归明确断言第二/第三 callback 零调用。

## 受约束对齐与 confusion network

1. 选择原生词时最完整的可用候选为时间 anchor；原生词中含多个中日字符或英语词时，
   只在其原生区间内分割。
2. 无词时候选先在 core 内建立受约束初始区间，再用 token 级动态规划对齐 anchor；
   插入/删除/替换都成为显式对齐操作。
3. 每个时间列按本机校准 model reliability、校准 token confidence、有声覆盖、词典命中和
   语言/格式合法性加权。缺少任一校准值时使用中性权重并在 provenance 中标明。
4. 每个最终 token 保存所有获胜候选的 candidate/model/revision/source index、原生或 DP 时间、
   vote weight、对齐列和校准来源。最终列区间单调且不重叠，不会把多个长文本压到同一时间点。
5. 获胜 token 必须来至候选。唯一允许新增的标准写法是已有候选 alias 命中显式
   `DeterministicTermRule` 时的 canonical，并同时保存 rule ID 和原 candidate 证据。

共识支持过低时选择一个当前最可信候选并标记 low confidence，不拼出流畅新句；
所有候选都被拒绝或无内容时，使用产品合同中带真实起止时间的中/日/英听不清标记。

## 持久化与验证

`ConsensusRepository` 把每份质量报告写入原 candidate，并以
`candidate_quality_gated`、`automatic_model_review_routed`、`automatic_consensus_adopted`
三类 `decision_events` 完整保存输入、输出和 rule version。最终 token 另存为
`candidate_id=null` 的当前共识层，其 provenance 仍指向同一 segment 内的实际 candidate；伪造或跨段 ID
会使整个短事务失败。

阶段专项测试覆盖固定无限重复、静音长文本、脚本/字符/时间异常、完整条件路由、
三语读音比较、原生词时 + 无词时 DP、校准权重、确定性术语、低可靠 fallback、三语
inaudible 和数据库审计。真实本机校准表由阶段 12 gold benchmark 产生。
