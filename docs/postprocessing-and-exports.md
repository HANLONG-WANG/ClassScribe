# 课程术语、严格标点、最终时间与导出

阶段 9 把共识文字变成可直接导出的课堂记录，同时保持两条硬边界：自动流程不得改写
`faithful_text` 的词序列，强制对齐不得借时间轴“修正”文字。所有运行仍为本地离线。

## 课程配置与本地材料

`config/schema/v1/course.schema.json` 定义 version 1 课程 YAML。课程、教师和术语记录
`canonical`、`reading`、`aliases`、`weight`、`source`、`confirmation`、语言和可选章节。
`classscribe.terminology.importers` 支持手工 YAML、UTF-8 TXT/Markdown/CSV、PPTX、PDF、
讲义、教科书章节及历史已确认术语。PPTX 只读取包内 slide XML；PDF 只调用本地
`pdftotext`；输入必须是有限大小的普通非符号链接文件。

材料自动抽取只返回 `suggested` 且权重不超过 0.3 的建议。建议可以进入有明确
“bias only”提示的 top-K 上下文，但不能自动添加到正文。确定性纠正还必须同时满足：

1. 术语已确认或为当前课程高权重；
2. 忠实层实际出现某个 alias；
3. 至少一个候选输出 canonical/alias，或读音编辑距离和声学读音支持共同过门。

因此规则只做 `alias -> canonical` 替换，不追加未听到的普通词。每次修正只写
`smart_corrected_text`，并在 `decision_events` 保存位置、前后文字、`rule_id` 和来源。

滚动上下文只取当前课程和语言最近一至三个已确认句段，加当前章节优先、近期频率和
权重排序的 top-K 术语，且有字符上限；不向任一模型传递整堂 90 分钟历史。

## 四层文字与三语标点

四层保持独立：`raw_text` 为模型原文；`faithful_text` 只接受可逆规范化和严格标点；
`smart_corrected_text` 允许上述确定性术语规则；`user_text` 只由用户写。导出选择固定为
`user -> smart -> faithful`，请求用户版而用户层为空时会显式记录实际回退层。

所有标点入口和持久化入口都执行：

```python
strip_punctuation_and_spacing(before) == strip_punctuation_and_spacing(after)
```

不相等的 proposal 被拒绝并回退，不存在自由生成文字的标点路径。

- 中文默认调用注册表固定 revision 的 FireRedPunc，再以 VAD 停顿、说话人变化和上扬语调
  添加边界。OpenCC 简转繁只在最终显示/导出变体运行，不回写任何文字层。
- 英语先检查模型原生大小写/标点；异常时才用 FireRedPunc。缩写、产品名和姓名必须保持
  原精确写法。评估分开报告 case-sensitive WER 和 punctuation F1。
- 日语先把 Granite proposal 的标点边界投射到已选字符序列，再补声学边界；没有可靠边界
  时才接受高置信序列标注标签。`。 、 ？ ！ 「」 （）` 做配对和密度检查。

FireRed worker 使用上游 `FireRedPunc.from_pretrained(...).process([text])` API，并分别返回
`origin_text` 和 `punc_text`；core 再执行字符不变门。具体模型库仍不进入 core 环境。

## canonical 最终时间

内部时间始终是 16 kHz master 上的绝对整数 sample，不累加浮点秒。选择顺序固定为：

1. 经校验的原生词时间；
2. MOSS 原生结构片段时间；
3. 通过安全门后由 Qwen3 ForcedAligner 得到的最终词时间；
4. VAD 粗时间。

ForcedAligner 仅处理小于 `min(registry_safe_window, 30 秒)` 的片段，并要求覆盖率合理、
无循环或漏句/异常字符、手动文字语言与声学语言一致、文字/有声时长比例正常。Qwen worker
使用上游 `Qwen3ForcedAligner.align(audio=..., text=..., language=...)`，只裁切 canonical
WAV，把局部秒转换回绝对 sample，原样回传输入文字。

`validate_timing` 检查 token 单调、不重叠、正时长、区间内、最终文字不变、VAD 有声覆盖、
对齐成本和句间 gap/VAD 偏差。任一精细来源失败即保留有效 MOSS/VAD 证据并标
`coarse_timing`；不会把错误或截断文字强压到时间轴。最终时间和回退原因写入
`token_spans`/`decision_events`，已有候选 provenance 合并保留。

## 六种导出

`classscribe.exports` 原子写出 TXT、Markdown、JSON、SRT、VTT、CSV，支持 faithful、smart、
user 三个请求层和逐句/可读段落视图。JSON 明确记录绝对 16 kHz sample、实际回退层、四层
文字、token 时间和 provenance。段落由长停顿、章节提示、说话人变化和语义边界形成，但不
改变字幕 token 时间。

SRT/VTT 只用所选文字层对应的最终句/词时间。中文/日文按字符数和 CPS，英文按词数、CPS
及自然句末分 cue；每条最多两行。具有相同 `protected_group` 的专名以及数字+单位先合成
不可分割单元，因此不会为了换行在内部任意切断。传统中文选项是渲染期变体，数据库仍保存
忠实简体来源。
