# ClassScribe 全项目源代码审查

原审查发现 26 项主要 bug 或接口合同缺陷，其中 P1 6 项、P2 20 项。此后已按请求实施全部修复及风险处理，详见 [修复记录与最终验证](code-audit-2026-09-30-fixes.md)。优先处理草稿丢失、跨词典写入、共识增删正文、暂停任务恢复和听写取消清理。另列低优先级库路径问题、尚未证实生产影响的风险，以及具体性能和维护建议。

审查日期：2026 年 9 月 30 日。基线提交：`b4273ac176c21e7a3a6cbd590630c993df765747`。原发现针对该提交的审查快照；审查阶段未修改业务源码。后续修复未提交或发布。下文原因和行号保留原审查证据，当前实现与验证以修复记录为准。既有未跟踪 `tmp/` 音频实验材料保持原样。

## 范围和证据说明

遍历项目 361 个源文件，共 77,820 行，包含 109 个测试源文件、252 个实现/样式/模板/启动脚本文件。后端、前端、worker、协议、IBus、模型清单工具和发布/诊断脚本均纳入。配置、协议 schema、打包清单、架构文档及历史审查作为辅助证据。依赖安装目录、生成的 frontend/dist、Git/代码智能内部数据和临时音频不作为项目源码。

使用 GitNexus 的 query/context/trace 与 Serena 的符号定义、引用互证。索引提交与当前基线一致，索引时间为 2026-09-25，包含 9,172 个符号和 530 个执行流。对动态调用、接收者类型缺口与跨进程边界另读实现及注册信息；没有把零图命中解释成无调用。没有进行 PDG/taint 全量分析，因此本报告不声称排除了所有安全问题。

Python 文件均进行了 AST 语法/结构遍历，结合 Ruff、mypy、现有测试和分模块源代码审查；重要异常/并发路径另运行隔离探针。测试文件既有全文阅读，也有结构、断言和重点场景核对，未把所有测试文件都宣称为逐行人工阅读。逐文件方式、行数与内容 SHA-256 见 [覆盖清单](code-audit-2026-09-30-files.tsv)。

P1 表示可能丢失/错误写入用户数据、改变最终正文或阻断主要工作流；P2 表示特定配置/异常/边界下的功能或结果错误；P3 用于低优先级库路径和维护问题。已复现表示实际执行当前代码并观察到所述机制，未等同于已测真实模型准确率或真实设备故障。源码确认项有明确触发合同和调用链，但没有执行完整端到端复现。

## 主要发现索引

| 编号 | 优先级 | 发现 | 主要位置 |
|---|---|---|---|
| A001 | P1 | 冲突处理请求期间输入的草稿会被旧快照覆盖 | [frontend/src/transcriptSaves.ts:147](/home/hubery-fedora/Projects/ClassScribe/frontend/src/transcriptSaves.ts:147) |
| A002 | P1 | 切换词典后，尚未关闭的编辑表单会写入另一词典 | [frontend/src/pages/GlossaryPage.tsx:159](/home/hubery-fedora/Projects/ClassScribe/frontend/src/pages/GlossaryPage.tsx:159) |
| A003 | P1 | 共识没有空词票，少数候选独有的插入词获得 100% 支持 | [backend/classscribe/consensus/alignment.py:76](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/consensus/alignment.py:76) |
| A004 | P1 | 只要有任意原生词时间，共识就丢弃不在 words 中的正文 | [backend/classscribe/consensus/alignment.py:101](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/consensus/alignment.py:101) |
| A005 | P1 | 暂停中的检查点在重启后无法恢复 | [backend/classscribe/jobs/state_machine.py:214](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/jobs/state_machine.py:214) |
| A006 | P1 | 关闭识别流报错时取消跳过状态恢复和租约释放 | [ibus/dictationd/classscribe_dictationd/session.py:188](/home/hubery-fedora/Projects/ClassScribe/ibus/dictationd/classscribe_dictationd/session.py:188) |
| A007 | P2 | RPC 超时释放异步锁，但实际推理线程仍运行，后续请求可并发进入同一模型 | [protocol/python/classscribe_protocol/rpc.py:149](/home/hubery-fedora/Projects/ClassScribe/protocol/python/classscribe_protocol/rpc.py:149) |
| A008 | P2 | 30 秒内的语言／说话人边界被提前退出吞掉，生产也未传入 LID 边界 | [backend/classscribe/audio/segmentation.py:191](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/audio/segmentation.py:191) |
| A009 | P2 | MOSS 整句时间 token 超过 18 个中日文字会导致 SRT/VTT 导出失败 | [backend/classscribe/exports/subtitles.py:144](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/exports/subtitles.py:144) |
| A010 | P2 | 合并句段会复活被用户清空的文字 | [backend/classscribe/api/service.py:800](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/api/service.py:800) |
| A011 | P2 | 局部重跑不会重新生成自动导出 | [backend/classscribe/classroom/pipeline.py:622](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/classroom/pipeline.py:622) |
| A012 | P2 | 自动导出把回退的粗时间标为aligned | [backend/classscribe/classroom/production.py:2073](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/classroom/production.py:2073) |
| A013 | P2 | 听写配置允许 4～5 个上下文片段，但高精度最终确认 RPC 只接受最多 3 个 | [protocol/python/classscribe_protocol/dictation.py:70](/home/hubery-fedora/Projects/ClassScribe/protocol/python/classscribe_protocol/dictation.py:70) |
| A014 | P2 | GStreamer 的设备错误 / EOS 消息没有运行中的 GLib 主循环负责分发 | [ibus/dictationd/classscribe_dictationd/runtime.py:727](/home/hubery-fedora/Projects/ClassScribe/ibus/dictationd/classscribe_dictationd/runtime.py:727) |
| A015 | P2 | 原生词 token 不含标点时，字幕完全撤销已计算的分 cue | [backend/classscribe/exports/subtitles.py:95](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/exports/subtitles.py:95) |
| A016 | P2 | 数字／单位／否定词冲突用集合比较，交换位置和重复次数可漏触发第三模型 | [backend/classscribe/quality/language.py:110](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/quality/language.py:110) |
| A017 | P2 | 英文 benchmark 数字正则优先匹配普通词，3.5 与 3 5 被算作零 WER | [backend/classscribe/benchmark/normalizers.py:9](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/benchmark/normalizers.py:9) |
| A018 | P2 | 缺失全部词时间被评分为 0 ms 误差，排名可胜过有正常误差的时间证据 | [backend/classscribe/benchmark/metrics.py:176](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/benchmark/metrics.py:176) |
| A019 | P2 | pyannote 没有覆盖当前结构段时也会强行指定无关说话人 | [backend/classscribe/structure/diarization.py:376](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/structure/diarization.py:376) |
| A020 | P2 | 设置页的保留期限和听写音频保存开关未接入执行路径 | [backend/classscribe/api/service.py:1414](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/api/service.py:1414) |
| A021 | P2 | 基准创建接口留下永远运行中的记录 | [backend/classscribe/api/service.py:1685](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/api/service.py:1685) |
| A022 | P2 | 清单集合更新失败会破坏既有完整bundle | [tools/model-manifest/classscribe_manifest_tool/generator.py:1100](/home/hubery-fedora/Projects/ClassScribe/tools/model-manifest/classscribe_manifest_tool/generator.py:1100) |
| A023 | P2 | 全局「打开草稿」不切换草稿所属课堂 | [frontend/src/App.tsx:122](/home/hubery-fedora/Projects/ClassScribe/frontend/src/App.tsx:122) |
| A024 | P2 | 队列把 0..1 的任务进度直接当百分数显示 | [frontend/src/pages/QueuePage.tsx:109](/home/hubery-fedora/Projects/ClassScribe/frontend/src/pages/QueuePage.tsx:109) |
| A025 | P2 | doctor默认忽略用户配置 | [backend/classscribe/doctor.py:28](/home/hubery-fedora/Projects/ClassScribe/backend/classscribe/doctor.py:28) |
| A026 | P2 | 桌面启动器忽略可配置的地址端口 | [packaging/desktop/classscribe-launcher:4](/home/hubery-fedora/Projects/ClassScribe/packaging/desktop/classscribe-launcher:4) |

## 主要发现详情

### A001 P1 冲突处理请求期间输入的草稿会被旧快照覆盖

- 位置：`frontend/src/transcriptSaves.ts:147`（151–165）、`:169`（173–203）；入口 `frontend/src/App.tsx:132`、`:143`。
- 触发：草稿收到 409，点击「读取服务器最新版」或「确认用草稿保存」；GET 未返回期间继续在文本框输入，随后请求完成。文本框和这些按钮均未禁止并发操作。
- 原因：两个函数在 await 前取 `draft`，请求完成或失败后仍以 `...draft` 回写，没有重新读取当前草稿，也没有草稿代际检查。新增字符被覆盖；rebase 还把旧文本 PATCH 并标记 saved。GET 期间点击「放弃草稿」也能被旧请求重新创建草稿。
- 验证：`/tmp/classscribe-audit-ui-probes/probe.test.tsx` 前两项直接使用实际 Zustand/保存模块。把 before 改成 typed while reading / typed while confirming 后完成延迟 GET，实际草稿恢复 before，PATCH 也发送 before。两个复现断言通过。
- 建议：每次完成请求重新读取同一草稿并保留最新 text，给被删除/重建草稿标识代际；服务器版本元数据与用户文本分别更新。确认保存时明确记录所确认版本，不能覆写 await 期间发生的编辑。
- 缺失测试：现有 `frontend/tests/transcriptSaves.test.ts` 只覆盖正常串行冲突恢复；增加读取、确认、失败、放弃与输入交错的测试。

### A002 P1 切换词典后，尚未关闭的编辑表单会写入另一词典

- 位置：`frontend/src/pages/GlossaryPage.tsx:159`、`:190`、`:205`、`:280`。
- 触发：在词典 A 点击编辑词条，在侧栏切到词典 B，再点击仍显示 A 词条的「保存词条」。
- 原因：`editing` 只保存 `GlossaryTerm`，没有绑定所属词典；侧栏只修改 selected，不清理 editing；`saveEdit` 使用当前 `glossary.id`。编辑数据因此 PUT 到 B；若 B 已有同 canonical/language，正常 upsert 语义会覆盖 B 的词条。若同时修改语言，又会以 B glossaryId 删除 A 的 termId，造成写入后删除失败的半完成操作。
- 验证：第三项真实 React/Vitest 探针确认切到 B 后 A 的编辑标题仍存在，保存请求为 `/api/v1/glossaries/b/terms`。没有执行真实数据库写入。
- 建议：编辑状态携带原 glossaryId/termId，保存始终使用固定目标；切换词典时清理或明确保留并标注原目标。语言迁移应有明确、原子的后端操作。
- 缺失测试：`frontend/tests/GlossaryPage.test.tsx` 只测试同词典打开编辑与材料导入；增加跨词典切换、删除当前词典、修改语言失败的场景。

### A003 P1 共识没有空词票，少数候选独有的插入词获得 100% 支持

- **位置**：`backend/classscribe/consensus/alignment.py:76`，`backend/classscribe/consensus/network.py:127`。
- **场景**：两个候选为 `Give 5 mg daily.`，另一个候选为 `Give 5 mg extra daily.`。对齐把缺失该词的候选直接跳过，分母仅计仍在该列的票，所以 extra 在该列的支持度为 1，而不是 1/3。
- **执行证据**：使用真实 QualityFeatureExtractor 后 resolve 输出 `Give 5 mg extra daily.`，strategy=`time_aligned_confusion_network`，low_confidence=False；所有最终 token 的 support_score 都为 1。两个候选中的一个多词也有同一机制。
- **影响**：自动忠实层保留少数模型的额外词，错误被标成高支持。存在投票缺词时不能将该列描述为一致共识。
- **结构证据**：GitNexus trace `_quality_and_review → ConfusionNetwork.resolve → align_candidates`，context 给出 resolve 与 fallback 的两个直接调用；Serena 确认 `_candidate_tokens` 被 align_candidates 调用，并核对对齐/投票实现。
- **建议／缺测**：把 deletion/epsilon 纳入每列的候选票及支持度，定义插入采用阈值或回退到完整候选。`test_consensus.py` 只验证等长替换、无效候选和重复回退；补 2 比 1 插入、删除、数字/否定词插入测试。

### A004 P1 只要有任意原生词时间，共识就丢弃不在 words 中的正文

- **位置**：`backend/classscribe/consensus/alignment.py:101`（提前返回在 :115）；`backend/classscribe/asr/models.py:194`；`backend/classscribe/quality/features.py:253`。
- **场景**：合法响应 normalized_text 为 `Give 5 mg daily.`，words 仅包含 `Give`，其合法时间覆盖整段。现有响应合同只检查时间范围和类型，没有检查词正文覆盖；质量门只检查时间覆盖。
- **执行证据**：该证据经真实 QualityFeatureExtractor 得到质量 1.0、issues=()、valid=True，resolve 最终输出 `Give` 且 low_confidence=False。
- **影响**：ASR 返回的完整原文被压成词时间列表中的子集，最终忠实层静默删词；后续强制对齐仅对删过的 final_text 重新对齐，不能恢复丢失文字。没有声称某个真实模型必定产生此响应，但该形状符合当前合同，缺陷已可执行复现。
- **结构证据**：GitNexus 的上述 trace 与 context；Serena `_candidate_tokens` 引用定位 align_candidates 对 token_sets 的唯一构造。
- **现有合同证据**：`tests/unit/test_body_asr.py:186` 的全文是“量子 CPU”，words 只有“量”“CPU”，解析测试接受该遗漏形状，却没有继续验证共识。`tests/integration/test_body_asr_workers.py:332` 的 FireRed 替身正文“今天学习 CPU”、timestamps 只有“今天”“CPU”，也接受部分时间证据。
- **建议／缺测**：采用 native timing 前验证 lexical token 序列与 normalized_text 一致；不一致应保留全文并回退粗时间，或拒绝原生时间证据。补“全文+仅前缀 words／部分 words／空 words”的组合回归测试。

### A005 P1 暂停中的检查点在重启后无法恢复

位置：`backend/classscribe/jobs/state_machine.py:214`（筛选状态）、`backend/classscribe/classroom/pipeline.py:600`（resume）、`backend/classscribe/jobs/state_machine.py:143`（begin_checkpoint）。用户在模型执行期间暂停，状态为 PAUSED 而当前检查点仍为 RUNNING，此时进程崩溃。重启 recovery_scan 不包含 PAUSED，保留悬挂 RUNNING 检查点；resume 只改作业状态；下一次 begin_checkpoint 拒绝 RUNNING，run_next 捕获异常返回 False，作业永久显示 RUNNING。临时 SQLite 探针输出 recover=()、run_next=False、job=running、checkpoint=running。GitNexus context(recovery_scan) 确认 ClassroomPipeline.recover 调用，Serena 读取状态机与恢复实现。建议重启时修复所有活动检查点，PAUSED 作业保持暂停，只重置其被中断检查点，补暂停中崩溃再恢复测试。

### A006 P1 关闭识别流报错时取消跳过状态恢复和租约释放

- 位置：`ibus/dictationd/classscribe_dictationd/session.py:188`（192–197）、`ibus/dictationd/classscribe_dictationd/daemon.py:304`（304–306）；可达错误源 `ibus/dictationd/classscribe_dictationd/runtime.py:289` 的 RPC stream_close。
- 触发：正在听写时 ASR worker 失联，用户 Esc 或 release ARMING 取消，recognizer.cancel 的 RPC 失败。
- 原因：Controller 的 finally 只清空部分字段，异常跳过后面的 publish(IDLE)；Service 没有 finally 释放租约，异常跳过 `_release_lease`。麦克风和消费者虽已停止，daemon 的 current 仍 listening、service session 仍占有 core lease。SchedulerLeaseClient 的续租任务也未停止，新的 ARMING 不能开始，课堂可持续被暂停，直到额外取消成功或进程退出。该问题是已修复 B020 后的异常清理遗漏：有续租/到期保护，但活着的 client 正在续租没有释放的会话。
- 验证：`/tmp/classscribe-audit-dictation-probe.py` 令 cancel 抛 worker disappeared，实际输出 `after cancel: listening controller session: None lease active: 1`。对始终报错的 recognizer，close 后仍 listening / active=1。生产 Manifest route 第二次 cancel 有机会成功，不应解释成所有 runtime 都永久无法释放。
- 建议：Controller 始终发布结束状态；Service 用 finally 释放租约并保留可重试清理，同时报告原 worker 错误。
- 缺失测试：`tests/unit/test_stage11_dictation.py` 的取消替身均成功；增加 stream_close timeout/错误响应，以及取消后可开始下一会话、续租停止、core 恢复课堂的检查。

### A007 P2 RPC 超时释放异步锁，但实际推理线程仍运行，后续请求可并发进入同一模型

- **置信度**：高，已用真实 RPCServer/QwenAdapter 与无模型 FakeModel 复现。
- **位置**：`protocol/python/classscribe_protocol/rpc.py:149-162`；`workers/qwen/adapter.py:192-195`；同类模式见 `workers/nemotron/adapter.py:338-341`。
- **场景**：驻留 Qwen 的 stream_push/flush 解码超过期限（生产 flush 为 3500ms），或跨连接 cancel；之后同一个驻留 worker 收到另一个推理请求。模型运算位于 `asyncio.to_thread`。
- **证据**：RPCServer 取消 adapter task 后立即返回并移除 `_tasks`；`async with _decode_lock` 随协程取消退出，然而 native/thread 中的 `transcribe` 不被 task.cancel 停止。Qwen 与 Nemotron 仅在 `await to_thread` 返回之后检查 cancelled；这一检查不能保证此前线程已退出。后续请求因此获得同一个 decode_lock，而首个模型调用还活着。Qwen batch 还临时修改共享 `model.max_new_tokens`，Nemotron cached decode 修改共享缓存/提示和模型 encoder 配置。
- **实际影响**：发生超时后，重试或后续听写可能与旧运算同时访问模型/缓存，造成状态竞争、额外显存占用、错误结果或后续 CUDA 失败。课堂 one-shot invoker 在失败后终止 worker 能减轻此问题；驻留听写进程不应依赖该隔离方式。
- **结构证据**：GitNexus trace：`RPCServer._handle_connection -> _dispatch -> _dispatch_adapter`；Serena 确认 `_dispatch` 来自真实 UDS handler，并确认 `QwenAdapter._decode_stream` 由 `_stream_push` 与 `_stream_flush` 调用。跨语言/协议边界通过实际 dispatch 源码与 producer/consumer 验证。
- **探针**：`/tmp/classscribe_workers_probe_bounded.py` 正常退出 0；首个请求 25ms 返回 `deadline_exceeded`，随后 `active_after_deadline=2`、`max_concurrent_inferences=2`。未调用 torch/真实模型；只复现并发进入 native callable 的事实，不声称已测真实 GPU 故障。
- **建议**：将模型执行权与 native callable 的真实完成绑定；取消时待线程完成且禁止新推理/卸载进入同一对象，或把该 worker 标记为不可复用并终止隔离进程。能够协作终止的 backend 可使用自己的 stopping callback；不可终止的线程不能当作已经停止。
- **缺失测试**：现有 `protocol/contract_tests/test_rpc_v1.py:test_rpc_deadline_and_cross_connection_cancellation` 使用可取消的纯 asyncio slow handler，未覆盖 `to_thread`。应增加超时/取消后新请求和 unload 的线程门闩测试，断言任意时刻 native callable 并发数不超过 1。

### A008 P2 30 秒内的语言／说话人边界被提前退出吞掉，生产也未传入 LID 边界

- **位置**：`backend/classscribe/audio/segmentation.py:191`；`backend/classscribe/classroom/production.py:534`；`backend/classscribe/classroom/production.py:611`、`:2174`（_dominant_language）。
- **场景**：20 秒录音前 10 秒日语、后 10 秒英语，LID 已准确给出两个 LanguageSpan，10 秒存在语言/说话人转换。make_transcript_chunks 在 remaining<=30s 时直接输出整段，完全不查看 cues；生产 _moss_structure 调 StructurePipeline.process 还漏传由已持久化 LanguageSpan 派生的 boundary_cues。
- **执行证据**：向真实 make_transcript_chunks 传 10 秒 LANGUAGE_SWITCH 与 SPEAKER_CHANGE，仍只得到 `(0,320000,end_of_speech)`；真实 _dominant_language 对这段选择 ja。生产 _transcribe 后续使用 segment.language 和 manual_language=True。
- **影响**：混语段被用某个单语路由处理；短录音和最后 30 秒的说话人转换被并成一个 speaker_id；原 LanguageSpan/SpeakerSpan/结构证据仍保存在数据库，缺陷发生在最终正文分片和路由。超过 30 秒的段即使没有提前退出，也会缺少生产 LID cues。
- **结构证据**：GitNexus context make_transcript_chunks 的两个调用方是 AudioPreprocessor.analyze 和 _build_natural_transcript_chunks；Serena 引用印证二者。已直接检查生产与 StructurePipeline 的调用和返回链。
- **建议／缺测**：强制语言／身份边界应先切分，再使用时长目标选择软边界；将 LID span 边界传进结构后正文切片。现有自然边界测试用 50 秒且优先选择说话人边界，缺 20 秒双语及最后短尾段。

### A009 P2 MOSS 整句时间 token 超过 18 个中日文字会导致 SRT/VTT 导出失败

- **位置**：`backend/classscribe/exports/subtitles.py:144`、`:151`；`backend/classscribe/classroom/production.py:1186`。
- **场景**：ASR 与可靠 MOSS 结构文本一致，无可用原生词时间时，生产选择 MOSS native timing，把一整句作为一个 AlignedToken。若该 token 超过 18 个中日文字，_wrap_units 的 range(1,1) 为空，default=1 将单个 unit 分成 `(整句, "")`；SubtitleCue 拒绝空行。
- **执行证据**：内存数据库写入 `selection_score=.9` 的 MOSS 结构记录，运行真实 ProductionStageRunner._forced_alignment→_export_segments→render_export(SRT)，得到一个 `moss_native_structure_timing` token，随后 `ValueError: subtitle cues require one or two non-empty lines`。未执行模型。
- **影响**：原生整句结构时间这条正常优先级分支无法导出长句；若任务要求自动字幕，该异常使导出阶段失败。
- **结构证据**：GitNexus context build_subtitle_cues 的直接调用为 _render_srt/_render_vtt；Serena `_wrap_units` 引用定位构建 cue，build_subtitle_cues 引用印证六格式导出入口。直接检查 _structure_timing→_forced_alignment 数据流。
- **建议／缺测**：单个不可分 unit 超长时保留一条合法非空行，或对整句时间证据进行安全细分后布局。补中日 19/36 字单 token、长 protected_group、MOSS timing 到生产自动导出的回归。

### A010 P2 合并句段会复活被用户清空的文字

位置：`backend/classscribe/api/service.py:800`（split的current）、`backend/classscribe/api/service.py:856`（merge的文本选择）。用 user_text or smart_corrected_text or faithful_text 将显式空字符串当作未编辑。用户把第一段清空，再合并连续两段，已删除内容重回新的 user/faithful/raw 三层。用真实 ClassScribeService 与临时数据库复现：第一段 user_text='' / faithful='removed'、第二段 faithful='kept'，合并后 user_text='removed kept'。ExportSegment.text_for 已明确区分 None 和 ''，所以这是不同操作间的合同不一致。建议按 is not None 选择用户层，统一文本层解析；补清空→合并和清空→拆分测试。

### A011 P2 局部重跑不会重新生成自动导出

位置：`backend/classscribe/classroom/pipeline.py:622`。retry_segment 重置该segment检查点，保持全局 automatic_exports 检查点 COMPLETED。已完成作业重跑后状态再次 COMPLETED，但默认导出仍为旧文字、旧时间。临时 SQLite 与真实 pipeline 代码跑完整任务，automatic_exports 调用次数初始1、重跑后仍1。GitNexus显示 service.retry_segment/rerun_segment→pipeline.retry_segment→queue.append；Serena引用核对service。建议重置依赖的导出检查点并保留历史导出版本/标记当前快照，补文字改变后的局部重跑自动导出测试。

### A012 P2 自动导出把回退的粗时间标为aligned

位置：`backend/classscribe/classroom/production.py:2073`、`backend/classscribe/exports/models.py:60`。ProductionStageRunner._export_segments构造ExportSegment时漏传timing_quality，因默认aligned使STRUCTURE/VAD粗时间在自动导出中被宣称精确时间。手动API导出已有正确传递，两个入口对同一数据库记录结果不一致。临时SQLite probe输出automatic质量aligned/coarse=False，manual质量structure/coarse=True。建议共用导出快照映射并始终传数据库timing_quality；补强制对齐失败/无词时间回退后自动与手动导出精度元数据一致测试。

### A013 P2 听写配置允许 4～5 个上下文片段，但高精度最终确认 RPC 只接受最多 3 个

- **置信度**：高，协议不一致已复现，IBus 生产传递链已互证。
- **位置**：`protocol/python/classscribe_protocol/dictation.py:70`；`protocol/python/classscribe_protocol/messages.py:202-210`；`protocol/python/classscribe_protocol/batch_audio.py:79-94`；`ibus/dictationd/classscribe_dictationd/runtime.py:375`；`ibus/dictationd/classscribe_dictationd/session.py:65,78,93`。
- **场景**：通过支持的 configure payload 设置 `rolling_context_segments=4` 或 5，使用 accuracy 模式持续听写，语义/硬切后已积累 4 个上下文片段，松开触发最终高精度确认。
- **证据**：DictationConfig 接受 1..5，session 以该配置构造 RollingContext。`RPCStreamingRecognizer._accuracy_decode` 原样把全部 `context` 放入 transcribe_pcm；RPCRequest 的构造校验要求 len(context)<=3；PCM 桥转出的 body-ASR 以及 pronunciation_context 也限制最多三句。因而这并非单纯 UI 数字与文案差异，而是有效配置会构造无效 RPC。普通 Qwen stream_flush 当前未执行此最多三句校验，不能将此项扩大成所有模式都失败。
- **影响**：较长听写在松开确认时失败，当前未提交的 final 文本可能需要用户重新操作；已累计的上下文反而阻断确认。默认值 3 不受影响。
- **探针**：`DictationConfig.from_mapping({'rolling_context_segments':5})` 成功；用四句构造 transcribe_pcm 抛 `ProtocolError: transcribe_pcm rolling context is invalid`。
- **建议**：统一一份上下文容量合同。若协议只支持 3，应在配置时拒绝 4/5 或在调用边界明确仅选择最近三句；若产品需要 5，应一并更新协议、PCM 桥、worker helper 与 schema。
- **缺失测试**：参数化 1..5 的完整 session -> accuracy finalization 组合测试，不能只分别验证配置范围和 RPC 严格性。

### A014 P2 GStreamer 的设备错误 / EOS 消息没有运行中的 GLib 主循环负责分发

- 位置：`ibus/dictationd/classscribe_dictationd/runtime.py:727`、`:736`；入口 `ibus/dictationd/classscribe_dictationd/main.py:152`。
- 触发：录音开始后 PipeWire/GStreamer 发布总线 ERROR 或 EOS，停止向 appsink 提供 sample。
- 原因：`bus.add_signal_watch()` / connect("message") 将处理挂到 GLib 默认上下文；dictationd 主入口只运行 `asyncio.run`，没有 GLib 主循环、上下文迭代或 bus poll/sync watch。new-sample 在流线程可以正常执行，使正常录音看似工作，但总线消息不会触发注册的 error_callback。
- 影响：设备断开路径不会自动调用 fail、停 source、释放 core lease；界面可能继续显示 listening 且实际已无音频，需用户干预。
- 验证：本机 `/usr/bin/python3` + 真实 GI/Gst 创建 Gst.Bus，挂与实现相同的 signal watch，post EOS 后仅运行 asyncio 时 callback=0；手动迭代 GLib 默认上下文后 callback=1。未访问麦克风或 GPU。
- 建议：以受控线程运行 GLib context，或将总线事件用 GStreamer sync handler / 有界 bus poll 转交 asyncio，并在 stop 注销与清理。
- 缺失测试：现有断麦克风测试直接调用假的 error_callback，绕过 Gst.Bus；增加实际 ERROR/EOS 到 daemon cleanup 的无硬件组合测试。

### A015 P2 原生词 token 不含标点时，字幕完全撤销已计算的分 cue

- **位置**：`backend/classscribe/exports/subtitles.py:95`；`backend/classscribe/exports/subtitles.py:154`。
- **场景**：19 个英文词有原生逐词时间，final_text 的句号来自原生正文或后续标点，词 token 不含该句号。这是合法且常见的 timing/text 分离：validate_timing 本来忽略标点检查正文一致。
- **执行证据**：真实 build_subtitle_cues 对 19 词正文加句号、19 个不带句号词时间返回一个 19 词 cue，超过函数自身的 16 词每 cue 和 8 词每行目标。
- **原因／影响**：_comparable 只去空白，因一个句号判定全文不匹配，再将 chunks=[全部units]；_wrap_text 只二分全文，长段与正文均出现时，字幕行长／阅读速度限制被整体绕过。
- **结构证据**：与 A009 相同 export context/Serena 引用，另检查 production _native_timing 使用忽略标点的 validate_timing。
- **建议／缺测**：先按 lexical 序列验证全文，再将标点边界投射到 timed tokens，保留可用逐词时间和分 cue。现有 subtitle 样例把句号写在最后 token 内，遗漏“标点只在 final_text”的长句。

### A016 P2 数字／单位／否定词冲突用集合比较，交换位置和重复次数可漏触发第三模型

- **位置**：`backend/classscribe/quality/language.py:110`、`:116`；`backend/classscribe/quality/routing.py:91`。
- **场景**：两个 55 词的高质量英文段仅在结尾交换“早上 5 mg、晚上 10 mg”与“早上 10 mg、晚上 5 mg”。数值及单位集合相同；全文差异 2/55<0.05，不满足 homophone_spelling_conflict 的 lexical 门槛；如果次模是因低 SNR 或术语复核已调用，而两候选质量高，第三模型被跳过。
- **执行证据**：真实 comparison 返回 lexical_distance=0.036364、high_value_conflicts=()、homophone_spelling_conflict=False；真实 ReviewRouter.tertiary 对两个 .9 quality report 返回 run_tertiary=False。
- **影响**：数值与时间、实体的对应关系改变也不会被识别为高价值冲突；否定词出现次数/位置同样有此盲点。
- **结构证据**：GitNexus context compare_language_candidates 调用方为 production _quality_and_review 和 QualityReviewPipeline.review；Serena _high_value_conflicts 引用定位比较结果。
- **建议／缺测**：比较有序数字/单位/否定事件和其附近上下文，至少保留次数与位置；不能只比较 set。现有高价值测试是两组不同数值、单位、人名，补同集合不同对应、重复/缺失一个数值、长段稀疏关键差异。

### A017 P2 英文 benchmark 数字正则优先匹配普通词，3.5 与 3 5 被算作零 WER

- **位置**：`backend/classscribe/benchmark/normalizers.py:9`。
- **场景**：参考 `Give 3.5 mg`，预测 `Give 3 5 mg`。普通词分支 `[^\\W_]+` 包含数字且排在数字分支之前，先消费“3”，再匹配“5”，小数整体分支无法运行。
- **执行证据**：两者 normalized_english_words 都为 `('give','3','5','mg')`；真实 score_text normalized_wer=0.0。只有提供额外 number entity 标注才可能从独立字段看出问题；rank_candidates 的主正文指标仍视为完全正确。
- **影响**：小数点/千位分隔读写错误被正文分数隐藏，模型排名和校准标签偏乐观。
- **结构证据**：GitNexus context raw_english_words 显示 normalized_english_words/comparison_units 与 benchmark CLI 流；Serena 引用确认 comparison_units 及当前归一化测试仅检查普通词和撇号。
- **建议／缺测**：先匹配完整数字或排除普通词分支中的起始数字，明确定义小数/分组规则。补 3.5、3 5、3,500、3500、负数/百分比是否应相等的显式合同测试。

### A018 P2 缺失全部词时间被评分为 0 ms 误差，排名可胜过有正常误差的时间证据

- **位置**：`backend/classscribe/benchmark/metrics.py:176`；`backend/classscribe/benchmark/ranking.py:61`、`:100`。
- **场景**：gold.words 有标注，prediction.words=() 或全部不匹配。_aligned_words 无配对，score_timeline 把缺测值变成 0.0，空表的 structural_errors 也为 0；排名没有词时间证据覆盖门。
- **执行证据**：参考有 hello 词、预测空 words 的真实 score_timeline 返回 structural_errors=0、word_boundary_mae_ms=0.0；其它指标相同时，真实 rank_candidates 把没有时间的模型排在 10 ms 有时间模型前，两者均 eligible=True。
- **影响**：无词时间证据被解释成完美准确，违背 constraint-first 的排名语义；会影响课堂自动选模使用的已保存排名。Gold 本来没有时间标注应单独处理为“不评分”，不是给无预测提供奖励。
- **结构证据**：GitNexus context score_timeline 的直接调用为 BenchmarkRunner._score_item；Serena 引用印证 runner 与 unit tests。已检查 runner 聚合与 production 本地 profile 使用路径。
- **建议／缺测**：报告 word_time_observation/matched_coverage 与 undefined MAE，有 gold 而缺 prediction 时失败或处罚；同等可评估数据再排名。补空预测、部分覆盖、完全错词、无 gold 时间四种边界。

### A019 P2 pyannote 没有覆盖当前结构段时也会强行指定无关说话人

- **位置**：`backend/classscribe/structure/diarization.py:376`、`:226`。
- **场景**：pyannote exclusive_spans 仅覆盖 0–2 秒，而 MOSS 粗段位于 3–4 秒，结构异常进入 pyannote fallback。_dominant_exclusive_speaker 仍把每个不相交 span 的 0 长度加进 durations；只要列表非空，max 就选一个标签，而不是保留 None。
- **执行证据**：两个 exclusive 标签 unrelated-A / unrelated-Z 均不与 3–4 秒相交，真实 helper 返回 unrelated-Z（所有时间权重为 0，按名称打破平局）。apply_pyannote_fallback 对该粗段因此设置此身份；生产后续从结构段选 speaker_id，不能凭另一个未覆盖该段的 speaker 轨道纠正。
- **影响／建议**：缺失说话人证据被伪装为已确定身份。只累计正交集，最大有效覆盖为零时返回 None，低覆盖应保留“不确定”及复核信息。
- **结构证据／缺测**：GitNexus context 显示 helper 唯一调用方 apply_pyannote_fallback；Serena 引用定位该调用。`test_structure_diarization.py` 的 fallback 样例均与 exclusive 正相交，补缺失部分语音／完全无交集／标签名不同的对照。

### A020 P2 设置页的保留期限和听写音频保存开关未接入执行路径

位置：`backend/classscribe/api/service.py:1414`、`frontend/src/pages/SettingsPage.tsx:59`。UI 保存 retention.derived_days 与 ibus.save_audio，但 update_settings 仅写 AppSetting，只有settings读回和classroom_queue消费AppSetting；dictationd独立配置与运行时不读这些值。GitNexus query(retention derived_days save_audio AppSetting)命中UI保存而无执行流；Serena AppSetting引用仅service与queue；全库文字核对同名字段仅UI。用户可收到保存成功却无自动清理/保存音频行为。建议建立显式可验证的运行配置合同/通知路径；未实现的选项显示为不可用。补跨进程设置应用测试，保留期限要有调度与清理验收。

### A021 P2 基准创建接口留下永远运行中的记录

位置：`backend/classscribe/api/service.py:1685`、`backend/classscribe/api/routes.py:375`。POST /benchmarks 返回202，create_benchmark写RUNNING记录后立即读回，没有运行器调度、任务队列或完成/失败处理。现有独立 BenchmarkRunner/CLI不消费这条run ID，因此轮询不会完成，也不能apply-ranking。GitNexus context显示唯一调用来自HTTP且唯一业务出边为benchmark读取；源代码全文核对无后台消费接口创建记录。建议绑定可取消的后台执行/导入现有已验收报告，或未实现前拒绝创建；补202→completed/failed端到端测试。

### A022 P2 清单集合更新失败会破坏既有完整bundle

位置：`tools/model-manifest/classscribe_manifest_tool/generator.py:1100`、`:1108`。write_release_bundle逐个替换成员，最后才替换bundle索引。旧bundle存在时，中途OSError/中断会留下新成员+旧hash，原本有效集合不可验证；单文件原子写不能提供整个集合的原子性。注入仅索引写失败，输出old bundle retained=True/member still matches index=False。GitNexus context确认_run_generate调用写集合，CLI在写后才verify。建议新目录完整写入验证后统一切换/不可变版本目录加原子指针，或仅允许空输出目录。补任意成员/索引失败和并发读旧集合的故障注入测试。

### A023 P2 全局「打开草稿」不切换草稿所属课堂

- 位置：`frontend/src/App.tsx:122`–123；`frontend/src/transcriptSaves.ts:6`–13；`frontend/src/pages/TranscriptPage.tsx:179`、`:205`。
- 触发：A 课堂的保存失败，在 B 课堂或其他页面点击全局 A 草稿的「打开草稿」。
- 原因：Draft 不持有所属 jobId，按钮只 selectSegment(id)+navigate(transcript)，currentJobId 仍 B。工作台加载 B 的 transcript/recording，却可以独立加载 A segment 的 detail。用户看到 B 音频和句段列表配 A 的编辑器；若 currentJobId 为空，只显示请先创建课堂任务，恢复的草稿无法通过按钮打开。
- 建议：草稿保留所属 jobId，或先 GET segment 取得所属课堂，再 setCurrentJob/selectSegment；拒绝 detail 与当前课堂不一致的组合。
- 缺失测试：`frontend/tests/App.test.tsx` 的打开恢复草稿测试只断言 page/selectedSegmentId，没有检查课堂、波形与编辑器一致。

### A024 P2 队列把 0..1 的任务进度直接当百分数显示

- 位置：`frontend/src/pages/QueuePage.tsx:109`；数据来源 `backend/classscribe/api/service.py:356`，对比 `frontend/src/pages/JobPage.tsx:148`。
- 触发：后台 job.progress=0.4、0.95 等普通运行任务。
- 证据：queue API 原样返回 job.progress，JobPage 乘 100 显示 40%/95%，QueuePage 却 Math.round(item.progress) 显示 0%/1%。队列几乎全过程显示没有进度，临近完成也仅 1%。
- 建议：共用百分比格式化函数，按 0..1 合同乘 100 并夹取范围。
- 缺失测试：`frontend/tests/QueuePage.test.tsx` 只有 progress=0；E2E workbench 的 queue 假数据反而用 progress=100，掩盖合同错误。用后端真实 0.4 / 0.95 验证两页面一致。

### A025 P2 doctor默认忽略用户配置

位置：`backend/classscribe/doctor.py:28`、`backend/classscribe/config.py:240`。doctor 不传 --config 时 load_config(None) 只验证默认文件；core使用 XDG config.yaml。临时XDG存储server.port=-1，stub仅外部依赖探测：doctor exit=0/config.status=ok，但core所用load_config实际路径抛ConfigError。建议与core一致默认使用 AppPaths.config/config.yaml，并在诊断输出记录实际验证配置来源。补未显式指定配置时读取XDG用户覆盖测试。

### A026 P2 桌面启动器忽略可配置的地址端口

位置：`packaging/desktop/classscribe-launcher:4`、`backend/classscribe/cli.py:46`。ServerConfig允许port改变与::1监听，但launcher固定127.0.0.1:8765探活并打开。同一用户合法配置port=9000或host=::1时服务能启动，桌面入口却报超时。建议由core输出运行端点或统一读取配置，IPv6 URL正确加[]；补自定义端口和IPv6启动器测试。

## 低优先级问题和待量化风险

这些条目与主要缺陷分开统计。未接入生产的库路径、尚未确认自然 UI 可达性的机制、以及需要真实模型量化的准确率影响，均在各项中限定。

### U01 硬切重叠上下文只存在于计划，生产请求丢弃它

- **位置**：`backend/classscribe/classroom/production.py:609`、`:1440`。
- **场景／证据**：make_transcript_chunks 对 95 秒连续语音生成带 1 秒双侧 audio_span 的硬切片；_moss_structure 只保存 core_span，而 _transcribe 重建 chunk 时把 requested 同时填给 core_span/audio_span，hard_split=False、重叠均为 0。因此生产 ASR 实际没有“硬切两侧上下文”合同，字词跨 30 秒边界时可能少识别音节。静态数据流确定，实际字词准确率需模型/音频验证。
- **结构证据**：已核对 production 定义/Serena overview、GitNexus make_transcript_chunks 调用链与构建请求的直接源码；context _transcribe 多重同名，未据歧义结果假定图完备。
- **建议／缺测**：持久化 chunk 音频范围与核心范围，以 audio_span 请求并将输出按 absolute token time 分配到唯一 core，再去重；当前重叠性质测试仅调用纯分片器，补生产 invoker 请求参数及跨界词测试。

### U02 同实例切换任务时旧控制响应污染新任务的查询缓存

- 位置：`frontend/src/pages/JobPage.tsx:58`–65（onSuccess 使用闭包 jobId）。
- 触发条件：A 的重试/暂停/恢复/取消 POST 尚未完成，在不卸载 JobPage 的情况下直接把 currentJobId 切到 B；随后 A 的请求返回。当前正常从 JobPage→队列→B 的导航会卸载旧 JobPage，未证明这条自然 UI 路径可达到上述条件，因此不列为已确认用户缺陷。
- 原因：Mutation observer 的 options 随 rerender 更新，onSuccess 使用当前 jobId，却未验证返回 job.job_id，于是把 A 的响应对象写入 `['job', B]`。
- 条件成立时的影响：B 的任务页面短时显示 A 的状态与ID，但后续控制 URL 仍指向 B；未来若加入不卸载的任务切换，此机制会导致状态错配。
- 验证：第四项真实 React/Vitest 探针中，A failed→retry，通过 store.setState 直接切到 B completed，再返回 A pending，实际 `client.getQueryData(['job','b']).job_id === 'a'`。探针证明机制，不证明当前导航可达。
- 建议：请求变量固定携带 jobId，成功响应按 `job.job_id` / mutation variables 填对应缓存；对返回身份作一致性检查。相同原则应用到 TranscriptPage 中仍依赖 selectedId 的异步操作。
- 缺失测试：JobPage 测试没有请求期间切 currentJobId 的情景。

### U03 旧 AlignmentRepository 路径按 candidate_id 去重，丢弃同候选其它来源词

- **位置**：`backend/classscribe/alignment/persistence.py:105`、`:62`。
- **执行证据**：两个前序 token 同 candidate_id、source_token_index 分别 0 和 1，_combined_provenance 输出只剩 index=1；apply 把这一个尾词来源广播到每个新对齐 token。
- **范围限制**：Serena 引用显示 AlignmentRepository 仅被包导出及 `tests/integration/test_postprocess_persistence.py` 使用，当前 production 已使用更细去重键和 sources_for_span，故不声称生产仍有同一个 provenance 缺陷。
- **结构证据**：GitNexus context _combined_provenance→AlignmentRepository.apply；Serena class 引用定位唯一实际测试调用。probe 已确认来源丢失。
- **建议／缺测**：复用生产的按 candidate+source_index+time 去重与 span 过滤逻辑，或取消漂移的第二套实现。现有测试只有一个旧 final token／一个来源，不能暴露覆盖。

### U04 英文句号不被重复句检查识别

- **位置**：`backend/classscribe/quality/loop.py:116`。
- **执行证据**：`Today we discuss a new topic in the classroom.` 连续重复 3 次，真实 inspect_decode_loop 输出 issues=()、maximum_repeated_sentence_count=1；3-gram 阈值要求 >3，短周期检测要求4轮，其它门也未触发。
- **影响／建议**：该函数明确检查重复句 >2，却漏了英文最常见的句号；加入避免误拆小数/缩写的 sentence segmentation。现有重复主题回归均是日语，应增加英语三次整句循环和正常小数/缩写对照。不能据此认定所有正常课堂三次重复都应硬拒绝，可先作为软复核证据。

### U05 同步环境构建中断残留 staging 的库级清理问题

- **置信度**：机制高可信，生产触发未确认，已用无下载 runner 复现；触发范围为会抛 BaseException 的显式同步构建/安装中断。
- **位置**：`backend/classscribe/models/environment.py:105-185`，特别是 `182-185`。
- **场景**：WorkerEnvironmentProvisioner.ensure 在 uv 已写出部分环境后收到 KeyboardInterrupt/SystemExit。不同于无法执行任何 finally 的 SIGKILL，这里是 Python 异常正常展开的路径。
- **证据**：staging 清理只在 `except Exception`，不是 finally；KeyboardInterrupt/SystemExit 不属于 Exception。下次 ensure 创建另一随机 staging，不检查或回收前次残留。
- **可能代价**：未完成的 CUDA/PyTorch 环境可能长期占据缓存；失败安装反复积累残留和磁盘压力。FastAPI threadpool 常规请求异常通常是 Exception，不能把该探针直接说成所有 HTTP 取消都触发同一 KeyboardInterrupt。
- **结构证据**：GitNexus `_source_sha256` 的 context 包含 ensure/resolve/inspect/complete 检查；Serena `WorkerEnvironmentProvisioner.ensure` 引用确认生产调用来自 `ClassScribeService.repair_model_environment` 与 `InstalledModelHealthChecker.__call__`。
- **探针**：runner 在 staging 写 1MiB sentinel 后抛 KeyboardInterrupt；异常展开后发现 `staging_residues=1`、`retained_bytes=1048576`。测试全过程在 TemporaryDirectory 内，退出后自动清理。
- **建议**：在 finally 中清理仍然属于本次构建且尚未发布的 staging；对崩溃遗留 staging 提供按锁和安全路径验证的回收策略。
- **缺失测试**：对 runner 抛 KeyboardInterrupt/SystemExit 参数化，断言 staging 清空、先前成功环境保持原样、重试可用。此项不同于历史 B033（模型清单生成器临时目录）；该文件不是历史问题所修复的路径。

限界：当前生产构建来自 API 线程，普通 HTTP 取消或主线程 SIGINT 不等于该工作线程抛 KeyboardInterrupt。因此归为 P3 库级清理改进，不计入 26 项主要缺陷。

## 性能与维护改进

### M01 FireRed 流式 VAD 把整次听写 PCM 留在内存，没有丢弃已处理的历史帧

- **优先级/置信度**：P2 / 高，静态生命周期与生产调用已互证。
- **位置**：`workers/firered/adapter.py:390-411`；`protocol/python/classscribe_protocol/adapter.py:102-104`；`ibus/dictationd/classscribe_dictationd/runtime.py:58-79`。
- **场景**：toggle 模式或长时间 hold 听写；VAD stream 在整次 session 打开一次，只在 session close 时关闭，语义/硬切只重建 ASR 流。
- **证据**：super.stream_push 将每个 PCM frame 追加到 bytearray；VAD 仅增加读取 offset，永远不删掉已处理前缀。检测只需要 400-sample frame 和未满帧尾巴。
- **实际代价**：仅此字节缓冲按 16000*2 bytes/s 线性增长，一小时约 115.2MB（109.9MiB），与模型常驻内存叠加；长录音不需要重复保留已分析的全部原始 PCM。
- **结构证据**：GitNexus `FireRedAdapter._stream_vad` context 显示 dispatch 调用与 StatefulAdapter.dispatch 下游；Serena 也确认 stream_vad 分支。IBus 生命周期由IBus 分区审查核对，关键 open/close 源码另已直接阅读。
- **建议**：处理后丢弃可回收前缀，只保留不足下一帧所需的尾巴，并把累计绝对 sample 游标独立保存，保证 overlapping 400/160 帧不改变。
- **缺失测试**：用轻量 fake detector 推送几分钟帧，断言内存缓冲不随时长增长，同时保持 detect_frame 的样本序列和 voiced 判定一致；当前测试只推 160/400 等少数样本验证 API。

### M02 Qwen 所谓 incremental streaming 每个窗口都重新转写全量历史 PCM

- **优先级/置信度**：P2 / 高，代价由实现推导；未测真实模型速度。
- **位置**：`workers/qwen/adapter.py:105-117,172-187`。
- **场景**：Qwen 默认 560ms 片段，用户连续说到默认 25s hard chunk。
- **证据**：每过阈值 `_stream_push` 都传 `bytes(pcm)`（自流起点的全部历史）给 `_decode_stream`；后者每次重建 WAV 并调用一次普通 model.transcribe，没有复用模型原生 streaming cache。整段被重复采样、写盘和推理。
- **实际代价**：按 560ms 截止，约 44 次调用累计处理 0.56*(1+...+44)=554.4 秒音频来显示约 24.64 秒输入，约 22.5 倍音频时长，再叠加最终 decode。实际计算比例依模型非线性开销而不同；这里只给出重复输入时长，不把它宣称真实延迟倍数。片段尾部的调用更昂贵，更容易触发 A007 的 deadline 路径。
- **建议**：优先接入 native streaming/cache API；如必须使用 batch 增量回退，应明确能力和成本，限制重解码窗口、频率并在背压时合并过时 interim 请求，保留最终确认的正确性。
- **缺失测试**：记录传给 fake transcribe 的累计输入样本总量和最大窗口，制定预算；当前 streaming 测试验证文本和时间坐标，没有复杂度/背压断言。

### M03 worker stderr 在 core 侧全量积累，应有明确内存上限

- **优先级/置信度**：P2 / 高，未运行真实日志洪泛测试。
- **位置**：`backend/classscribe/models/worker_process.py:100-101,156-158,175-180`。
- **场景**：长驻留 worker 重复输出警告，或 untrusted remote-code backend 在 stderr 输出大量内容。
- **证据**：启动时创建 `process.stderr.read()`（无 size）；StreamReader.read(-1) 为等待 EOF 累积全部内容，不因逐段排空而设总量上限。长驻留进程直到卸载才 EOF；读取任务在 core 中保存完整输出。
- **实际代价**：worker 日志总量转化为 core RAM 占用，隔离进程的 stderr 可以消耗 orchestrator 内存；不是仅模型自身的内存压力。终止后完整 bytes 还要 decode 成 str，短时占用进一步增加。
- **结构证据**：GitNexus WorkerProcess.stop context 覆盖 resident.start_entry / stop_process / release_idle / end_dictation；该 context 明确 lower-bound、receiverTyping=1，因此补读 SandboxedModelInvoker._drop 与 InstalledModelHealthChecker._run，未按缺失边断言无调用。
- **建议**：持续消费 stderr，但只保留有界末尾缓冲和丢弃字节计数；显示完整错误前也采用有界脱敏输出。只有确需持久诊断时写独立有限大小日志。
- **缺失测试**：轻量子进程输出超过缓冲容量，断言 core 保留量固定、保留末尾关键错误、不会卡住 pipe。

### M04 来源/依赖更新与修复备份永久保留，缺少可操作的环境缓存回收

- **优先级/置信度**：P3 / 中高，物理字节成本与 uv hardlink/reflink 策略有关。
- **位置**：`backend/classscribe/models/environment.py:56-62,85-100,180-181,349-392`。
- **场景**：worker lock/source 多次升级，或重复修复损坏环境。
- **证据**：目标包含 lock hash + source hash，旧目录永久保留；repair 将 damaged target 移到 `.damaged-*`，随后构建新副本，也没有任何保留策略或删缓存入口。source hash 包含 README 等非运行文件，因此文档更新也能生成新环境目录。
- **实际代价**：旧 CUDA/Python package 版本及 damaged backups 留在用户 cache，增加长期磁盘管理负担，且用户模型删除不会回收环境。相同依赖版本在同文件系统可能由 uv hardlink/reflink 去重，不能直接把逻辑目录大小累加成真实占用；依赖版本变化或部分构建仍会产生新实体数据。
- **建议**：增加显式缓存大小/版本展示及回收操作，避开活跃 worker 挂载中的环境，并保留有限可回滚版本/诊断备份；考虑将运行影响文件与不影响执行的文档分别纳入来源身份。
- **缺失测试**：有限版本保留、正在使用环境不被删、损坏备份超限回收，以及 hardlink/reflink 场景实际占用统计。

### 应用配置 持久合同与资源上限

- 合并配置所有权：YAML、AppSetting、dictation IPC与UI默认值有多个入口。A020、A025、A026 是其具体代价，统一有效配置查询与更新回执。
- 为全局checkpoint（segment_id=NULL）与glossary(language,canonical)提供数据库唯一约束或显式串行upsert。SQLite包含NULL的多列unique允许重复全局checkpoint；glossary lookup index不是unique。常规队列owner减少现阶段可达性，应列为幂等/并发维护成本，而非声称已观察重复记录。
- 多个async HTTP路由直接执行SQLite/文件hash/导出/诊断subprocess。大结果或存储慢时会阻塞同一event loop、SSE和听写租约控制，迁移确定的阻塞操作到有界线程执行并限制请求体/导出规模。
- PipelineEventBroker每job保留上限，但_events/_sequence/_recent/_stage_activity字典没有全局淘汰。完成/删除数千作业的常驻服务内存随历史作业增长，应在完成/删除时删除或建立LRU。

### 前端与桌面生命周期

1. `ibus/engine/classscribe_ibus_engine/runtime.py:72`：保存 GLib.timeout_add 的 sourceId，在 engine destroy 中 source_remove；否则每创建一个输入上下文就遗留永久返回 True 的 timer，并持有 controller/client/bridge/engine，长期会话会积累对象。当前只在 focus_out 中停止请求，没有停止 timer。建议加 engine 生命周期替身测试；没有做桌面实机长时间测试。
2. `frontend/src/pages/TranscriptPage.tsx:190`、`:371`：虽然 summary 去掉 token，仍一次获取并渲染所有 segment，长课堂/多小时稿件会使 DOM 与 timeline span 很大。按段分页或虚拟化句段列表，保留固定时间轴与选择语义；用几千句段验证输入与播放响应。
3. `frontend/src/api.ts:52` 和各页面下载：普通 fetch、XHR 上传和诊断下载没有统一的超时/取消/可见错误。下载诊断 `void downloadDiagnostics()` 无 catch，失败只成为 unhandled rejection。建议统一操作错误显示；不要让用户在网络异常时只能重复点击。
4. `frontend/src/store.ts:28`、`:59`、`frontend/src/pages/UploadPage.tsx:83`：与保存/导入模块已有 try/catch 不同，普通工作台初始化与导航直接读写 sessionStorage。禁用存储或容量耗尽会使导航/模块加载抛错。建议提取容错的 storage adapter，并用 SecurityError/QuotaExceededError 测试。
5. `frontend/src/pages/GlossaryPage.tsx:203`：修改词条语言是先 upsert 新语言、再 DELETE 旧词条的两次独立请求。网络中断或删除失败会留两个身份；新语言已存在时也会先覆盖目标词条。建议后端单一迁移API，确认身份冲突并在同一数据库事务处理。
6. 跨层流式合同需组合测试：worker 返回值→RPCStreamingRecognizer→StablePrefix→EngineController，使用会修订旧假设的连续响应而非只追加独立 token；VAD stream 生命周期覆盖多次 ASR hard/semantic cut。上下文容量合同见 A013；FireRed VAD 的 PCM 内存增长见 M01。

### 预处理 上下文与验收统计

- `backend/classscribe/audio/pipeline.py:83` 先 analyze_channels 选 best channel，随后 `backend/classscribe/audio/qc.py:204` 的 build_report 再完整分析同一个多通道 PCM；长录音×多声道会重复一次 Python 逐样本遍历。可以直接传已计算的 channel metrics，保持报告一致。生产目前只调用 build_report 一次，主要针对公共预处理 API。
- `backend/classscribe/terminology/context.py:85` 仅截断 rendered 字符串，返回的 confirmed_segments/keywords 仍是未截断值，调用方若使用结构字段而不是 rendered，就不受 max_characters 限制。当前 production 另行 _bounded_recent_context 限制正文，且关键词有 top_k，所以是合同一致性改进，不能声称生产目前无界。
- `backend/classscribe/benchmark/acceptance.py:44` 的“真实90分钟/5分钟”时长用 max(end)-min(start)，不检查同音频 gold 样本的连续覆盖；GoldCoverage.inspect 又直接累加重叠样本时长。稀疏或重复标注可使覆盖声明偏乐观。建议按音频联合区间统计，把录音时长、人工标注覆盖、已推理覆盖分别明确，补间隙/重叠样本。未将此升级为接受门已实测绕过结论。

## 验证结果

| 检查 | 本次结果 | 限制 |
|---|---|---|
| Python 主套件 `pytest -q` | 665 通过、2 跳过、1 失败，45.43 秒 | 两个真实 CUDA/模型测试因未配置模型而跳过 |
| 主套件失败项 | `test_rpm_install_upgrade_remove_preserves_user_data` | `rpm --dbpath <临时目录> --initdb` 无法创建 `.rpm.lock`，Permission denied；归为当前执行环境限制，未据此判定业务缺陷 |
| 模型清单工具套件 | 152 通过，4.39 秒 | 使用本机 fake Hub/临时文件，没有联网下载模型 |
| 前端 Vitest | 19 个文件、73 项通过 | 使用已安装本地运行器 |
| TypeScript、ESLint、Vite build | 通过 | 本机 Node 22/pnpm 11 与项目要求 Node 24/pnpm 10 不一致，pnpm 正式 check 被 engines 拒绝；上述直接运行结果不替代声明工具链验收 |
| Ruff lint | 通过 | 当前冻结环境 ruff 0.16.5 |
| mypy strict | 238 个配置范围源文件通过 | 另对 11 个 worker 的 worker.py/adapter.py/healthcheck.py 独立 strict 检查，全部通过 |
| Ruff format --check | 20 个文件不符合格式，363 个通过 | 未自动改写；这是现有 CI 格式门问题，未算入主要 bug 数量 |
| 架构边界检查 | 通过 | 静态边界检查不替代运行时验证 |
| 全源文件遍历 | 302 个 Python AST 解析无错误，其余源/样式/模板/脚本纳入内容审查 | 浏览器 E2E 源码已读但未执行；未加载真实模型或访问麦克风/GPU/用户数据 |
| 隔离探针 | 复现草稿/词典竞态、取消清理、Gst.Bus 分发、共识/字幕/评分、恢复/重跑/合并、配置诊断、清单更新与线程超时等机制 | 探针有的刻意断言现有错误行为，不能当作修复后通过的回归测试 |

最初在受限沙箱中运行主套件时，本机 socket/异步路径失败或挂起；终止后经自动审批在沙箱外重跑，得到上述完整主套件结果。RPM 失败仍存在，没有将其掩盖为套件全通过。未执行模型真实推理验收、物理麦克风断连验收或长时间桌面测试。

## 源文件覆盖汇总

| 目录 | 源文件数 |
|---|---|
| backend | 134 |
| docs | 5 |
| frontend | 50 |
| ibus | 13 |
| packaging | 8 |
| protocol | 16 |
| scripts | 7 |
| tests | 76 |
| tools | 19 |
| workers | 33 |

详细清单区分全文审查、模块/符号审查、测试断言审查和自动遍历，并保留源内容哈希。源文件遍历与现有测试通过都不能保证不存在其它缺陷。

## 建议修复顺序

先修 A001–A006 的文本/状态完整性；随后修线程超时后的模型复用、混语切片和字幕异常，并统一重跑后的导出快照。基准评分问题应在再次采用本地排名前修复并重新生成受影响评估。配置/启动/诊断入口统一和资源上限治理可随后推进。上述为原审查建议；现已实施并补回归场景，详情见修复记录。审查阶段 changed_count=0 的结果属于原证据，不用于声明修复后的变化范围。
