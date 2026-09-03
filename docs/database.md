# 数据库与持久化状态

## SQLite 初始化

权威数据库位于 `${XDG_DATA_HOME}/classscribe/classscribe.sqlite3`。所有连接强制启用：

```text
PRAGMA journal_mode=WAL
PRAGMA foreign_keys=ON
PRAGMA synchronous=FULL
PRAGMA busy_timeout=5000
```

SQLAlchemy 2 metadata 使用稳定命名约定；Alembic 初始迁移位于
`backend/classscribe/db/migrations/versions/`。生产迁移必须显式传入 XDG 数据库 URL，
默认 Alembic URL 只指向 `/tmp`，防止误改真实数据。

## 表与所有权

| 表 | 权威内容 |
|---|---|
| `recordings` | 原始来源名／SHA-256／UUID 路径、sample 数、声道、采样率和 QC |
| `jobs`、`job_checkpoints` | 作业状态、阶段、进度，以及按阶段／片段／参数哈希限制重试的 checkpoint |
| `speech_regions`、`language_spans`、`speaker_spans` | VAD、语言、说话人和重叠的 canonical sample 区间；speaker span 带窗口、model revision 和 provenance |
| `structure_segments` | MOSS/pyannote/VAD 的粗结构、声学事件、局部/全局匿名说话人、fallback/overlap 和不可提升为最终文本的证据 |
| `speaker_display_names` | 仅限 job 的匿名说话人显示名；不修改原 diarization，也不建立跨课程身份 |
| `transcript_segments` | 原始／忠实／智能纠正／用户文本层、质量、粗／精时间和 `version` |
| `asr_candidates`、`token_spans` | 模型 revision、解码／质量／推理信息、逐 token 时间和 provenance |
| `decision_events` | 自动或人工修改的输入、输出、actor、规则版本和时间 |
| `glossaries`、`glossary_terms` | canonical、reading、aliases、语言、权重、来源和确认状态 |
| `model_installations` | repository/revision、路径、SHA-256、环境、实测显存与健康 |
| `benchmark_runs`、`benchmark_items` | gold、预测、指标、硬件、参数与排名 |
| `dictation_sessions` | 仅在用户显式允许历史时持久化的听写记录 |
| `glossary_materials` | 课程材料显示名、受控相对位置、哈希、来源与抽取计数 |
| `export_artifacts` | 原子导出格式、文本层、视图、受控相对位置、哈希与大小 |
| `profile_settings`、`app_settings` | benchmark 门控的本机选模 profile 与版本化设置组 |

所有时间轴表对非负、单调 sample 有数据库约束和索引。数据库枚举以字符串和 CHECK
约束存储。外键根据语义使用 CASCADE、RESTRICT 或 SET NULL。

阶段 6 迁移增加结构证据、job-local 显示名，并为 `speaker_spans` 增加
`window_ordinal/source_revision/provenance_json`。`structure_segments` 有数据库级
`adopted_as_final = 0` CHECK；显示名有 `identity_scope = 'job'` CHECK。逐窗 replace 在一个
事务中只删除同一 job/window 的旧结构和 speaker span，异常回滚不会影响其它窗口。

阶段 7 的正文存储不覆盖 `transcript_segments` 三层文本。自然 chunk 的 core 区间只负责建立
空的 canonical segment；每次模型结果追加到 `asr_candidates`，保存 raw/normalized text、固定
revision、完整 decode/seed、原始置信度、推理指标、warnings 和角色/窗口质量特征。原生字词
时间追加到 `token_spans` 并保持绝对 sample/provenance；重跑用 supersession 链而非覆盖旧证据。

阶段 8 复用现有表而不建第二套时间轴。质量四域特征、hard-validity、未校准门分和
循环重跑 directive 存入 `asr_candidates.quality_features_json`；raw confidence 仍与
`confidence_calibrated=null` 分离。质量、第二/第三模型路由和最终共识分别追加带 rule version
的 `decision_events`。当前最终 token 使用 `candidate_id=null` 区分于原候选 token，但
`provenance_json` 必须列出同 segment 内的 candidate/model/revision/source token/原时间，或为显式
inaudible marker。重算只替换当前最终 token 层，不删除候选和旧 decision event。

阶段 9 继续复用四层文字列。严格标点写 `faithful_text`，只有 smart 仍与旧 faithful 相同时才
同步，否则要求为 smart 层提供独立通过字符不变门的结果；`user_text` 永不由自动流程写入。
确定性术语修正只写 `smart_corrected_text`，差异位置、alias/canonical、规则 ID 和词典来源进入
`decision_events`。课程 YAML/材料词保存在 `glossaries/glossary_terms`，自动抽取项固定低权重且
`user_confirmed=false`。

最终时间只替换 `candidate_id=null` 的当前最终 `token_spans`，候选 token 和旧决策不删除；每个
新 token 合并原 candidate provenance，并记录 native/MOSS/Qwen/VAD 时间来源。精细对齐通过时
写 `canonical_timing_adopted`；失败写 `alignment_fallback`、`TimingQuality.STRUCTURE` 和明确
`coarse_timing` 原因。

阶段 10 为 job 持久化完整创建选项，并让活动 segment 通过 `is_active` 与
`supersedes_segment_ids_json` 表达拆分/合并版本链。checkpoint 唯一范围是
`(job_id, stage, checkpoint_key, segment_id)`，因此同一阶段的不同片段不会碰撞；自动导出
artifact、材料、设置和 profile 也由 Alembic 迁移创建。SSE、浏览器缓存或后台 task 均不是
恢复状态，core 重启只从这些数据库行重建。

## 可恢复事务

`JobStateMachine.run_checkpoint` 用三个短事务隔离一次片段执行：先提交 running/attempt，
再把片段输出与 completed 原子提交；异常时回滚片段输出，并在新事务中只把当前
checkpoint 标为 retryable/failed。服务重启把遗留 running checkpoint 恢复为
retryable，并按阶段顺序和 position 返回第一个未完成项。pause、resume、cancel、retry
均是显式状态转换，完成操作可重入。

## 乐观锁与审计

`transcript_segments.version` 同时由 ORM version guard 和审计服务的条件 UPDATE 保护。
浏览器必须提交读到的 version；rowcount 为零时返回 `SEGMENT_VERSION_CONFLICT`，不能
last-write-wins。`AuditService` 对三层文本编辑和候选采用写入 `decision_events`。
采用候选不可软删除；未采用候选只设置 `deleted_at`，新候选可用
`supersedes_candidate_id` 构成版本链。

拆分/合并只停用旧 segment 并新增带 supersession 的活动 segment。撤销/重做同样要求客户端
提交当前 version，并为每次变化追加 decision event；历史候选、token 和审计不会被就地改写。
