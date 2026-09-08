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

## 重复异常与硬拒绝

重复异常是可采用、需复核的软异常。日语、中文不再仅凭短主题词出现超过 3 次判异常；
n-gram 提示要求至少 6 个字符的片段出现超过 3 次且覆盖至少 60% 的 token 位置。
英语对应最低长度为 3 个 token。至少 4 轮的连续循环、整句反复、压缩重复和输出停滞仍会提示。
重复导致的输出密度过高也保留候选；静音幻觉、非法字符、语言脚本不符和非法词时等独立问题仍可硬拒绝。

共识优先使用没有重复异常的可用候选。所有可用候选均重复时，依次按重复异常项数更少、
其他质量分更高、模型可靠性更高选取一个完整候选，保留原有标点并标记待复核，禁止混合拼接。
单候选显示红色「疑似重复」标签，多候选全部重复显示「所有候选均疑似重复」。
点击句段查看触发原因、重复片段及模型；多模型一致不能解除异常。空候选不参与投票。
全部候选为空或有独立硬错误时，才生成带时间范围的「无可用转录」标记。

重复问题仍可产生 `RetryDirective` 请求更短且连续覆盖的分片、切换模型复核，但
`reject_candidate=false`；原候选继续保留。与原区间不一致的复核分片仍不接受。

启动时会恢复旧 `quality-gate-v1` 仅因重复问题作废的历史句段，不重跑模型。
恢复仅处理已结束任务，检查人工编辑审计、文本层和最近的候选集合，保留旧决策并记录恢复事件。
自动处理也会增加版本号，因此不以版本号是否为 1 判断是否有人编辑。恢复可重复执行。

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
