# 结构转录与说话人

## 结构文本的边界

`classscribe.structure` 处理 12 分钟、4 秒重叠的绝对 sample 结构窗口。MOSS-TD 返回
`start_sample/end_sample`、局部说话人、粗文本和声学事件。粗文本的固定角色为：

```text
coarse_timeline_consensus_candidate_boundary_reference
```

它只能用作粗时间轴、初始说话人、后续共识候选和短句段边界参考。RPC、领域模型和
数据库三层都保存 `adopted_as_final=false`；`structure_segments` 与最终
`transcript_segments` 分表，不能把结构文本直接提升为忠实正文。

默认说话人策略为 `expected_speakers=auto`、`prior_min=1`、`prior_typical=2`、
`max_speakers=12`、`allow_overlap=true`。`ordinary_class` 将 typical 限制在 1～2；
`group_discussion` 可保持 auto 或提供 1～12 的大致人数。人数是先验和上界，不是强制把
证据不足的声音合并到较少标签的理由。

## MOSS 与 pyannote 路径

MOSS worker 固定官方实现的完整 Git commit，以 Transformers `local_files_only` 加载本地
snapshot；每个结构窗口先裁成 canonical 16 kHz mono S16LE 临时 WAV，再通过官方消息构建、
生成和 transcript parser API 处理。生成在线程中执行，并通过 token callback 响应取消。

下列确定性异常会触发窗口级 pyannote Community-1 回退：worker 失败；至少 2 秒语音却没有
结构段；至少 5 秒语音且覆盖率低于 35%；说话人数超过配置上限；窗口内重复比例超过 25%；
空文本比例至少 80%；或在禁用 overlap 时检测到重叠。弱措辞本身不是结构异常，留给后续
正文共识处理。

pyannote 同时返回 regular 和 exclusive diarization 以及 speaker embeddings。普通时间轴
优先 exclusive；regular track 中两个及以上说话人相交的原子区间被记录为真实 overlap。
这些区间从 exclusive 持久化 span 和 embedding 支持音频中减去，并为每位参与者单独保存
`overlap=true` 证据。结构段在真实 overlap 内使用 `speaker_global=null`，不会伪造唯一说话人。

任一 worker 最多按窗口重试两次。MOSS 异常而 pyannote 成功时保留 MOSS 粗文本、替换异常
说话人归属；pyannote 也失败时保留异常 MOSS 粗结构；两者都失败则仅为该窗口生成可重试的
VAD 粗区间。其它已经完成的窗口不会被删除或回滚。

## 跨窗口 speaker stitching

pyannote 为每个局部说话人选择最多三个、不含 overlap、每段至少 1 秒的支持区间；信号质量
低于 0.7、标为 overlap 或不足两个 observation 的 embedding 不参与可靠匹配。局部 centroid
与本作业内全局 centroid 计算余弦相似度，并组合五分钟尺度的时间相邻分数及重叠窗口中
文本/绝对时间一致的同人约束。

默认可靠门为余弦 `>=0.72`、第一与第二候选 margin `>=0.05`，每窗保持一对一映射。强同人
约束可覆盖 embedding 不足；其余低可靠或一对一冲突一律创建新的 `SPEAKER_nn`。每个决定均
输出候选分数、观察数、余弦、margin、原因和全局标签。diagnostic payload 还记录 overlap link、
ID switch、switch rate、窗口路径、重试、异常和去重决定。

相邻窗口只在绝对时间确有相交且文本高度相似时判为重复；胜者依次按结构质量、非空文本
长度和稳定窗口顺序确定，失败者整段丢弃，绝不拼接两个模型字符串。完成 stitching 后，
说话人变化与粗句末会反馈给阶段 4 的 8～30 秒自然正文切片器；没有自然边界的 30 秒 hard
cut 仍保留双侧音频上下文。

## 持久化与身份范围

`StructureRepository.replace_window()` 在单个短事务内替换一个窗口的
`structure_segments` 和 `speaker_spans`，写前检查窗口号、canonical 音频范围、单调性、
绝对 sample provenance 和粗文本角色。结构片段保存 model/revision/request/window/source
index、选择分、原始置信度、声学事件、fallback/exclusive/overlap 和绝对区间。

UI 改名只 upsert `speaker_display_names(job_id, speaker_global_id, display_name)`；原始
`speaker_local_id`、pyannote/MOSS span 和全局匿名标签均不修改。`identity_scope` 数据库约束
固定为 `job`，所以默认没有跨课程永久身份识别。未来可选本地声纹模板必须是显式、独立的
用户选择，不能复用此表暗中扩展身份范围。

## 验证

阶段 6 测试覆盖 90 分钟精确 sample 时轴的 8 个 12 分钟窗口、7 个重叠区去重、speaker
switch 指标、低可靠新建、真实 overlap 分段、多个无重叠 embedding、MOSS/pyannote
窗口故障隔离、数据库事务回滚和 job-local 改名。生产 adapter 以伪造重型模块执行完整
load→infer→unload 路径；真实模型安装和本机质量/VRAM 基准仍由阶段 12 的 gold gate 决定。
