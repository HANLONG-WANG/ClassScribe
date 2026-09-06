# 需求—测试追踪矩阵

本矩阵覆盖阶段 0 冻结的全部 46 条产品要求。阶段 0 的“合同检查”验证需求已被准确
冻结并具有验收落点；后续阶段必须把“计划证据”替换或补充为实际测试路径。只有实际
证据通过，才表示对应运行时能力完成。

| 需求 | 规范／当前实现证据 | 自动测试或人工验收 | 计划完成阶段 |
|---|---|---|---|
| `PRD-CLS-001` | 产品需求 §1.1 | 90 分钟夹具导入、恢复和导出 E2E | 4、10、12 |
| `PRD-CLS-002` | 产品需求 §1.1；机器合同 `language_modes` | 四语言模式课堂 E2E | 7、10、12 |
| `PRD-CLS-003` | 产品需求 §1.1 | 1、2、多人及重叠语音 gold 样例 | 6、12 |
| `PRD-CLS-004` | 产品需求 §1.1 | 无人干预全流水线 E2E | 9、10、12 |
| `PRD-CLS-005` | 产品需求 §1.1；UI 措辞规范 | 跳过校对直接导出 E2E；UI 测试 | 10、12 |
| `PRD-IBUS-001` | 产品需求 §1.2；ADR-0001 | Fedora IBus 集成测试 | 11、12 |
| `PRD-IBUS-002` | 产品需求 §1.2；机器合同 | 四语言听写 E2E | 11、12 |
| `PRD-IBUS-003` | 产品需求 §1.2；UI 措辞规范 | 按住／预编辑／松开提交 UI 集成测试 | 11、12 |
| `PRD-IBUS-004` | 产品需求 §1.2 | IBus source 与 Wayland portal 实机验收 | 11、12 |
| `PRD-IBUS-005` | 产品需求 §1.2 | 超窗口连续听写边界 gold 测试 | 11、12 |
| `PRD-ARCH-001` | 产品需求 §1.3；ADR-0001 | 依赖规则静态检查与协议合同测试 | 1、5、12 |
| `PRD-ARCH-002` | 产品需求 §1.3；机器合同 `product_surfaces` | 配置/API 模式隔离测试 | 1、10、11 |
| `PRD-MODE-001` | 产品需求 §2；机器合同 | `test_frozen_enumerations`；四模式 E2E | 0、10、12 |
| `PRD-MODE-002` | 产品需求 §2；机器合同 | `test_frozen_enumerations`；选择器测试 | 0、5、12 |
| `PRD-MODE-003` | 产品需求 §2；UI 措辞规范 | 手动主模型触发 fallback 测试 | 5、8、12 |
| `PRD-MODE-004` | 产品需求 §2；UI 措辞规范 | 严格模式禁止备用 worker 测试 | 5、8、12 |
| `PRD-MODE-005` | 产品需求 §2 | 简体、中英混说、独立繁转测试 | 7、9、12 |
| `PRD-MODE-006` | 产品需求 §2；ADR-0004 | bootstrap→本机 gold 排名迁移测试 | 5、12 |
| `PRD-TEXT-001` | 产品需求 §3；ADR-0003；机器合同 | 三层不可覆盖与差异持久化测试 | 2、9、12 |
| `PRD-TEXT-002` | 产品需求 §3；ADR-0003 | 标点不改字、术语差异来源测试 | 9、12 |
| `PRD-TEXT-003` | 产品需求 §3；机器合同 | `test_language_specific_inaudible_markers`；范围格式测试 | 0、8、12 |
| `PRD-TEXT-004` | 产品需求 §3；ADR-0003 | 低质量夹具只生成听不清标记 | 8、12 |
| `PRD-SPK-001` | 产品需求 §3 | 匿名标签、改名与审计关系测试 | 6、10、12 |
| `PRD-QUAL-001` | 产品需求 §4；验收清单 | 无人工事件的全流水线 E2E | 10、12 |
| `PRD-QUAL-002` | 产品需求 §4 | 90 分钟故障注入／重启恢复测试 | 2、10、12 |
| `PRD-QUAL-003` | 产品需求 §4 | 四模式无校对导出矩阵 | 10、12 |
| `PRD-QUAL-004` | 产品需求 §4；ADR-0003 | 时间轴属性测试、范围测试、模型对齐测试 | 1、8、12 |
| `PRD-QUAL-005` | 产品需求 §4；ADR-0003 | 禁止长字符串共识静态规则；短片段测试 | 8、12 |
| `PRD-QUAL-006` | 产品需求 §4 | 五类质量故障夹具测试 | 8、12 |
| `PRD-QUAL-007` | 产品需求 §4；ADR-0003 | 逐词 provenance 完整性测试 | 2、8、9、12 |
| `PRD-QUAL-008` | 产品需求 §4 | IBus 分块边界词级差异测试 | 11、12 |
| `PRD-PRIV-001` | 产品需求 §5；ADR-0002；机器合同 | `test_local_only_privacy_policy`；断网 E2E | 0、2、3、12 |
| `PRD-PRIV-002` | 产品需求 §5；ADR-0002；机器合同 | 网络命名空间审计与依赖扫描 | 0、2、3、12 |
| `PRD-PRIV-003` | 产品需求 §5；ADR-0002 | 缺模型安全失败；离线安装后运行测试 | 3、12 |
| `PRD-PRIN-001` | 产品需求 §6；PR 模板；ADR-0003 | canonical timeline 属性与静态依赖测试 | 1、12 |
| `PRD-PRIN-002` | 产品需求 §6；PR 模板；ADR-0003 | 短片段上限与禁长文本投票测试 | 8、12 |
| `PRD-PRIN-003` | 产品需求 §6；PR 模板；ADR-0003 | 原生能力路由、对齐质量门测试 | 5、8、9 |
| `PRD-PRIN-004` | 产品需求 §6；PR 模板；ADR-0004 | 禁业务硬编码模型的静态检查 | 1、5、12 |
| `PRD-PRIN-005` | 产品需求 §6；PR 模板；ADR-0004 | 单重型 GPU 锁与 IBus 优先抢占测试 | 5、11、12 |
| `PRD-PRIN-006` | 产品需求 §6；PR 模板；ADR-0003 | 逐词来源 schema／属性测试 | 2、8、12 |
| `PRD-PRIN-007` | 产品需求 §6；PR 模板；ADR-0003 | 标点 token 恒等与术语差异测试 | 9、12 |
| `PRD-PRIN-008` | 产品需求 §6；PR 模板；ADR-0004 | 本机 gold 自动选模测试 | 12 |
| `PRD-NONGOAL-001` | 产品需求 §7；README；UI 措辞规范 | README/UI/包描述禁用措辞扫描 | 0、10、13 |
| `PRD-NONGOAL-002` | 产品需求 §7；README；UI 措辞规范 | 对外文案人工审查与禁用措辞扫描 | 0、10、13 |
| `PRD-NONGOAL-003` | 产品需求 §7；README；ADR-0004 | 排名来源字段测试与文案审查 | 0、5、12、13 |
| `PRD-NONGOAL-004` | 产品需求 §7；README；ADR-0003 | 忠实层写入权限与不可覆盖测试 | 0、2、9、12 |

## 阶段 0 合同测试

运行：

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

该测试解析机器合同与 schema、冻结统一枚举和隐私策略、检查语言专用听不清标记，
并保证本文件覆盖产品需求中的每一个稳定需求编号。

## 阶段 1 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-ARCH-001/002` | 独立 core、worker、dictationd、IBus engine、portal 和统一协议包；产品形态枚举 | `tests/unit/test_architecture.py`、`tests/integration/test_process_health.py`、`protocol/contract_tests/test_protocol.py` | 后续 RPC、数据库和产品流水线 |
| `PRD-MODE-001/002/003/004` | Python 枚举与机器合同一致，配置 profile 保持手动/fallback 语义文档 | `test_python_enums_match_machine_contract`、配置解析测试 | 阶段 5/8 的实际选模与 fallback |
| `PRD-QUAL-004` | 16 kHz int64 sample 工具、半开区间与绝对 offset | `tests/unit/test_timeline.py` 的 90 分钟和 Hypothesis 任意 sample 往返 | 模型输出和持久化时间轴在阶段 2/8 接入 |
| `PRD-PRIV-001/002/003` | 配置只允许 `runtime_offline=true`，core 仅允许 loopback，进程边界不含网络／云端依赖 | `tests/unit/test_config.py`、`scripts/check_architecture.py` | 阶段 2/3 网络命名空间和运行时断网 E2E |
| `PRD-PRIN-001` | `classscribe.timeline` 与协议时间字段规则 | timeline 单元／属性测试 | 后续所有表与 worker 消息接入 |
| `PRD-PRIN-004` | worker 独立工程，core 依赖清单和 AST 禁止具体模型库 | architecture check；14 个 frozen uv lock 检查 | 阶段 5 注册表执行 |
| `PRD-PRIN-005` | `one_heavy_worker=true` 和 IBus 优先级成为配置不变量 | config schema/model tests | 阶段 5/11 实际调度与抢占 |
| `PRD-PRIN-008` | 文档和配置明确阈值仅为 bootstrap | 配置完整解析及架构文档审查 | 阶段 12 本机 gold 校准 |

阶段 1 还新增 `scripts/check_architecture.py`，在 CI 中验证仓库完整结构、所有 worker 的
六项必需文件、共享协议依赖、协议版本字段，以及 core／桌面进程的禁止导入边界。

## 阶段 2 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-QUAL-002` | WAL/外键 SQLite、持久 job/checkpoint、逐片段事务、重启扫描和参数级重试 | `test_restart_resumes_first_incomplete_checkpoint_without_state_loss`、worker crash/重试测试 | 阶段 10 的真实 90 分钟全流水线故障注入 |
| `PRD-QUAL-004` | 所有 span 表存 int64 sample，并有非负／顺序 CHECK 和 timeline 索引 | database schema/constraint tests + stage 1 timeline properties | 阶段 8 多模型时间对齐 |
| `PRD-QUAL-007` | candidate/token provenance 字段、decision events、自动／人工编辑审计、采用候选保护 | `test_old_browser_edit_gets_explicit_conflict_and_audit_is_preserved`、candidate adoption tests | 阶段 8/9 逐词实际决策来源 |
| `PRD-TEXT-001` | transcript 三层列、version 乐观锁、审计事件；automatic 禁改 user layer | audit/locking integration tests | 阶段 9 文本处理实际写入 |
| `PRD-SPK-001` | speaker global/local ID 与 span 持久结构 | 完整表/列 schema test | 阶段 6/10 改名 API 与说话人模型 |
| `PRD-PRIV-001/002` | 256-bit 0600 token、loopback+Bearer ASGI、正文/秘密递归脱敏、无上传诊断、受限凭证环境文件 | `tests/unit/test_security.py`、`test_observability.py` | 阶段 3/12 进程级断网 E2E |
| `PRD-PRIV-003` | core/runtime 无模型下载依赖，模型安装表只记录本地状态 | architecture check 与 schema test | 阶段 3 离线工件安装验证 |
| `PRD-IBUS-005/QUAL-008` | dictation session 默认不落盘、临时音频清理和 daemon/mic/OOM/socket 安全失败接口 | `test_dictation_privacy.py`、`test_recovery.py` | 阶段 11 实际音频分块去重 |
| `PRD-PRIN-001/003` | DB/审计只持久化 canonical samples；候选时间异常整份拒绝，对齐失败回退结构粗时间 | database constraints、candidate timeline/alignment tests | 阶段 8/9 原生时间与 forced aligner |
| `PRD-PRIN-006/007` | token provenance、decision input/output/rule version、分层文本与乐观锁 | complete schema columns、audit tests | 阶段 8/9 共识、标点和术语实际规则 |
| `PRD-NONGOAL-004` | 忠实层不会被 user layer 覆盖；旧版本条件更新冲突；automatic 禁改 user layer | audit/locking tests | 阶段 9 禁自由改写的处理链验证 |

阶段 2 的迁移测试逐表逐列核对规范字段，执行 Alembic upgrade/check/downgrade；安全测试
直接以 ASGI scope 验证非 loopback、无 token 和错误 token，避免只检查配置而没有检查
实际中间件。三级删除测试仅在临时 XDG 根运行，并证明 derived-only 保留导出和决策。

## 阶段 3 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-PRIV-001/002` | 只读 Fedora doctor；运行时三套 offline 变量；Bubblewrap worker 取消网络 namespace，只暴露精确模型／音频／输出／socket | `test_fedora_checker_reports_full_matrix_without_mutating_system`、`test_remote_code_worker_sees_only_explicit_paths`、systemd/RPM 静态审计 | 阶段 12 的整条真实模型流水线网络 namespace 审计 |
| `PRD-PRIV-003` | 运行期 resolver 无 downloader，逐文件复验；缺失／篡改返回 `MODEL_NOT_FULLY_INSTALLED` | `test_runtime_missing_or_tampered_files_fail_without_downloader` 在 socket 构造被禁止时通过 | 阶段 5 的实际 worker RPC 错误传播 |
| `PRD-PRIN-004` | manifest 固定 repository/revision/worker，完整 commit 和逐文件 hash；模型不进业务代码或 RPM | 坏 revision/SHA、remote-code snapshot、RPM 文件清单测试；architecture check | 阶段 5 注册表能力与执行路由 |
| `PRD-PRIN-005` | 每个 worker 在独立 frozen uv 环境执行自己的 PyTorch CUDA probe；不检查完整 Toolkit；沙箱 GPU device 必须显式列举 | 9 组 `test_each_isolated_worker_exposes_its_own_cuda_probe` 和 sandbox path 测试 | 阶段 5 GPU lease 与阶段 11 IBus 抢占 |
| `PRD-NONGOAL-003` | 安装审计记录固定 revision、来源、环境、逐文件 hash、短音频健康结果；升级列出代码／模型变化，不以厂商数字选模 | `test_install_is_atomic_audited_offline_and_persisted`、`test_upgrade_diff_rollback_and_delete` | 阶段 12 本机 gold 排名 |

阶段 3 还以实际 `rpmbuild` 生成两个版本，并通过独立 RPM 数据库完成 install → upgrade →
erase；测试核对全部规范安装路径，证明 RPM 不拥有任何 XDG／模型路径且用户 sentinel 保留。
干净 XDG E2E 启动真实 Uvicorn，只在 `127.0.0.1:8765` 提供健康端点并生成 0600 token。

## 阶段 4 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-CLS-001` | 90 分钟 canonical master 元数据、结构窗口、VAD/LID/正文切片和 durable checkpoint resume | `test_ninety_minute_master_runs_vad_lid_and_both_slice_policies_without_drift`、90 分钟结构属性、重启 DB 测试 | 阶段 10/12 的真实 90 分钟全流水线模型推理 |
| `PRD-CLS-002/MODE-001` | 手动 zh/ja/en 完全绕过 LID；auto_mixed 只经稳定 LID 路由 | manual bypass、双窗滞回、单术语不切换和 raw probability tests | 阶段 7/10 的实际三语 ASR |
| `PRD-QUAL-004/PRIN-001` | FFmpeg master frame count 定义 16 kHz int64 时轴；所有 VAD/LID/window/chunk 使用绝对半开 samples | 11 格式真实 FFmpeg normalize、Hypothesis 1 sample～90 分钟、local reset 拒绝、DB range rollback | 阶段 8/9 的模型词时间和最终对齐 |
| `PRD-QUAL-005/PRIN-002` | 正文只生成 8～30 秒 core chunk；hard max 保留双侧 0.8～1.5 秒 context；token+绝对时间去重 | continuous-speech coverage/no-hard-cut 与 boundary dedup tests | 阶段 8 的实际候选边界共识 |
| `PRD-PRIN-003` | 结构窗口与正文句段为不同类型；VAD、speaker/pause/punctuation/language cues 保持原生边界 | type separation、boundary priority、speaker embedding stitching tests | 阶段 6 的真实 MOSS/speaker 输出和阶段 8 原生时间优先级 |
| `PRD-PRIV-001` | 原件 UUID 物理名、0400、SHA-256，本地 FFmpeg 暂存和原子发布；无上传／网络路径 | real import/hash/mode/master tests 与 symlink/unsupported failure tests | 阶段 10 上传 API 和阶段 12 断网全 E2E |

阶段 4 的格式矩阵以真实 FFmpeg 编码并重新读取 WAV、FLAC、MP3、M4A、AAC、OGG、Opus、
MP4、MOV、MKV、WebM；QC 同时验证多声道选择与显式等权下混，未使用降噪、AEC 或响度
增强。speech/language DB 替换在写前校验，因此越界或非单调输入不会删除旧证据。

## 阶段 5 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-ARCH-001` | 4-byte MessagePack UDS、强类型 request/response、11 个隔离 worker family、deadline/cancel/crash supervision | `test_rpc_v1.py`、`test_every_worker_serves_versioned_rpc_lifecycle`、worker crash 测试 | 阶段 6/7 各候选的具体 adapter |
| `PRD-MODE-002/003/004/006` | 20 项 versioned registry、课堂/IBus profile、enable/disable/upgrade/rollback/rank/install/benchmark mutation | `test_model_registry.py` 的完整 pinned/lock、bootstrap 顺序和原子 history 测试 | 阶段 8 的 manual-primary fallback/strict-single 执行和阶段 12 gold 改排 |
| `PRD-QUAL-004/PRIN-001/003` | batch RPC 只收 16 kHz 只读受控路径和绝对 sample range；响应 segment 复核绝对范围；能力决定原生时间路由 | frame/payload/path/segment contract tests；真实 model 绝对时间 E2E | 阶段 8/9 多候选时间对齐和 forced alignment 门控 |
| `PRD-PRIV-001/002/003` | socket 0700/0600 + same-UID；worker 启动强制三套 offline 变量；model path/revision 仅本地 | socket 权限/路径逃逸、offline subprocess、缺文件结构化错误和 `test_real_neural_asr_load_transcribe_unload` | 阶段 12 完整网络 namespace 审计 |
| `PRD-PRIN-004` | 所有 repository/revision/language/task/mode/backend/window/VRAM/缺陷来自 registry；core 不导入 model runtime | registry schema/lock hash、architecture AST、profile query tests | 阶段 6/7 adapter 实现继续遵守同一入口 |
| `PRD-PRIN-005` | 单 owner lease，0/10/20/30/40，IBus 阻断新课堂 dispatch，当前课堂只在 segment boundary 让出；同模型复用 | `test_dictation_preempts_classroom_only_at_segment_boundary`、dispatch block/reuse、三 residency policy tests | 阶段 11 接入真实 dictation session |
| `PRD-NONGOAL-003` | registry 区分 bootstrap public facts 与 local gold；raw confidence 不跨模型比较；阶段 12 前拒绝 `quality_probability` | nested probability rejection、benchmark source/ranking tests | 阶段 12 每模型×语言×场景校准 |

OOM 合同测试证明 cleanup → batch 1 → shorter window → SDPA → verified precision/quantization →
registry smaller model 的每次参数指纹唯一，耗尽后在有限次数内记录失败。候选 executor 的
peak active count 恒为 1。真实模型验收使用 fixed-revision、CPU int8、local-files-only 的
Whisper Tiny reference 和离线合成语音，经实际 worker UDS 完成 load → transcribe → unload；
该 disabled reference 不在任何生产 profile。

## 阶段 6 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-CLS-001/003` | 90 分钟按 12 分钟窗口运行 MOSS 结构、pyannote regular/exclusive/overlap、跨窗去重与匿名 speaker stitching | `test_ninety_minute_pipeline_uses_twelve_minute_windows_without_overlap_duplicates`、pyannote overlap/parser tests | 阶段 12 真实 90 分钟多说话人 gold 和本机 VRAM/RTF |
| `PRD-SPK-001` | 原始 local label、job-local global label 和 UI display name 分层；改名不改 diarization；无跨 job identity | `test_structure_persistence.py` 的原证据不变、upsert 和另一 job 隔离测试 | 阶段 10 WebUI 改名交互 |
| `PRD-QUAL-002` | MOSS/pyannote 各窗口有限重试；双失败只产生该窗口 VAD 粗证据；逐窗短事务 | `test_worker_failures_are_isolated_to_one_window_and_leave_retryable_coarse_structure`、事务回滚测试 | 阶段 10/12 完整流水线重启与真实 worker crash |
| `PRD-QUAL-004/PRIN-001/003` | 所有结构、regular/exclusive/overlap、embedding support 和 speaker span 使用绝对 sample；model/revision/request/window provenance 完整 | structure parser 越界拒绝、90 分钟单调性、数据库范围约束、协议扩展测试 | 阶段 8/9 最终正文词级对齐 |
| `PRD-QUAL-005/PRIN-002` | MOSS 文本固定为粗时间轴/共识候选/边界参考；跨窗质量胜者整段保留，不拼接；结构 speaker/句末反馈 8～30 秒正文切片 | coarse role 三层约束、dedup score tests、`test_natural_transcript_chunks_split_on_stitched_speaker_change` | 阶段 8 多候选短片段共识 |
| `PRD-PRIV-001/PRIN-004` | 两个生产 adapter 仅加载本地 snapshot，重型依赖仍在独立 worker lock；core 不导入 MOSS/pyannote | production-adapter fake-runtime load→infer→unload、architecture AST、lock SHA tests | 阶段 12 真实模型断网全 E2E |

说话人可靠门要求每个局部标签至少两个高质量、无重叠 observation；余弦、候选 margin、
时间相邻和强重叠同人约束均进入诊断。低可靠、一对一冲突和仅 overlap 的说话人会新建
job-local ID，不为了人数先验强制合并。真实 overlap 从 exclusive span 与 embedding support
中减去，并按参与者持久化 `overlap=true`。

## 阶段 7 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-CLS-002/MODE-001` | 三种手动语言的 registry profile、自然 chunk 单模型 pipeline、六个离线生产 adapter；FireRed 双层禁止日语 | `test_manual_language_primary_pipeline_end_to_end` 三语矩阵、三语 espeak WAV adapter E2E、FireRed route rejection | 阶段 10 的 `auto_mixed` 全流水线和阶段 12 固定权重 gold |
| `PRD-MODE-003/004` | primary 和可选 review/expert 独立运行；实验项必须显式 enable；结构候选不可正文 dispatch | planner 顺序、未安装/重复/structure 拒绝、Qwen JA/Fun-ASR explicit gate tests | 阶段 8 按 auto-best/manual-primary/strict-single 触发复核 |
| `PRD-MODE-005` | 中文 raw text 保留嵌入 ASCII；繁体只从忠实简体层派生 OpenCC 独立变体 | FireRed spoken-WAV `CPU`、`test_opencc_variant_is_derived_after_faithful_simplified_and_preserves_english` | 阶段 9/10 保存和展示繁体导出选项 |
| `PRD-QUAL-004/PRIN-001/003` | context/core 都是绝对 sample；FireRed 原生词时偏移；无原生时间明确 request-span；响应范围/模型身份/时长上限复验 | native absolute word test、真实 WAV 精确 32,000-sample 裁剪、越界/过长 response rejection | 阶段 8/9 多候选对齐和 forced aligner |
| `PRD-QUAL-005/PRIN-002` | 自然短片段逐模型运行；默认 batch 1/no mixed、greedy、seeded、时长 token/字符上限；MOSS 结构文本始终 nonfinal | decode tampering 矩阵、overlong generation、structure nonfinal、六 worker generation kwargs | 阶段 8 confusion network，不进行长字符串投票 |
| `PRD-QUAL-006` | Fun-ASR 默认关闭且 expert-only；四连 token n-gram 在返回候选前拒绝 | `test_funasr_real_wav_is_expert_gated_and_rejects_detected_generation_loop` | 阶段 8 完整循环/静音/脚本/覆盖 QA |
| `PRD-QUAL-007/PRIN-006` | candidate 保存固定 revision/decode/metrics/warnings/角色/窗口；原生 token 保存绝对 sample 和 provenance；重跑保留 supersession | `test_natural_segment_and_candidate_persist_decode_metrics_words_and_supersession` | 阶段 8/9 最终逐 token 决策 provenance |
| `PRD-PRIN-004` | pipeline 只查询 registry；core 不导入模型库；worker 独立 lock 固定 FireRed/Qwen/Transformers 上游提交 | architecture AST、全部 dependency lock SHA、model registry tests | 阶段 12 以本机 gold 更新排序，不改变边界 |

三语适配器测试以 espeak-ng 和 FFmpeg 生成真实中文、日语、英语语音媒体，随后验证生产代码
实际裁剪和传递的 canonical WAV；大型权重用确定性 API 替身，避免把不可分发 checkpoint 纳入
仓库。Qwen forced-Japanese 实验 flavor 明确复用同一官方 1.7B revision；Fun-ASR 使用包含原生
模型支持的固定上游 Transformers commit。所有 raw confidence 保持模型内证据，未产生统一
质量概率或自动采用结论。

## 阶段 8 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-QUAL-003/006` | 声学/模型、文本、时间、多模型四域特征；3～10 n-gram、最短周期、压缩、速率和 prefix 停滞硬拒绝 | `test_infinite_anata_loop_is_rejected_then_split_with_full_coverage_and_model_switch`、静音/脚本/时间异常矩阵 | 阶段 12 在真实模型 gold 上调阈值 |
| `PRD-MODE-003/004` | 仅规定条件运行第二/第三候选；循环时全覆盖短窗+换模型 | 正常片段 fallback callback 零调用；术语/时间/MOSS/SNR 第二路由；数字/单位/否定/姓名/同音第三路由 | 阶段 10 按 UI 选模模式接入完整作业 |
| `PRD-QUAL-004/PRIN-001/003` | 所有候选限制同 canonical 区间；原生词时优先，无词时用受约束 token DP；最终列单调不重叠 | `test_native_word_time_anchor_and_dp_untimed_candidate_make_monotonic_columns`、跨区间拒绝、DB 最终 token 测试 | 阶段 9 Qwen ForcedAligner 与 coarse fallback |
| `PRD-QUAL-007/PRIN-006/007` | confusion network 综合本机 reliability、校准 token、声学覆盖、词典、脚本；每 token 来源或显式术语规则；全路由审计 | 权重胜出、无规则不增标准词、伪造 candidate ID 事务拒绝、三类 decision event 集成测试 | 阶段 12 本机校准器与概率可靠性图 |
| `PRD-QUAL-001/NONGOAL-002` | 无可靠共识时只选一个现有候选并低信心，或产生带真实时间的三语 inaudible；不用生成模型补写 | 三方分歧单候选 fallback、中/日/英 marker 精确文本和 provenance 测试 | 阶段 10 UI 低置信显示与阶段 12 端到端覆盖 |

阶段 8 共识代码不导入长段 `SequenceMatcher`，架构检查对该目录强制此规则。原
raw confidence 仅记录而不参与跨模型加权；本机校准不可用时权重为中性值并明确标记
`locally_calibrated=false`。此边界保证阶段 12 之前不将 bootstrap 分数伪装为可靠概率。

## 阶段 9 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-TEXT-001/002/PRIN-007` | 四层文字独立；标点字符不变；术语只在候选/读音声学证据下 alias→canonical 并只写 smart | Hypothesis 标点不改字、三语路由、术语无插入与 persistence audit 测试 | 阶段 10 UI 编辑/撤销和阶段 12 gold 术语召回 |
| `PRD-MODE-005` | FireRedPunc 中文+声学边界；英文 native-first+词典保护；日语 Granite 投射→声学→tagger；繁体仅导出变体 | 三语 fallback/保护/投射、case-sensitive WER、punctuation F1、FireRed official API adapter 测试 | 阶段 12 真实课堂标点 gold |
| `PRD-QUAL-004/PRIN-001/003` | native→MOSS→gated Qwen→VAD；短段循环/覆盖/脚本/语言/比例门；单调/范围/覆盖/成本/VAD-gap 验证 | timing priority、高成本/改字拒绝、coarse fallback、Qwen canonical crop+绝对 sample 测试 | 阶段 12 本机真实 aligner 成本阈值 |
| `PRD-CLS-005/QUAL-003` | TXT/MD/JSON/SRT/VTT/CSV 的 faithful/smart/user 和逐句/段落；用户层空时 user→smart→faithful | 六格式矩阵、JSON layer/timeline、原子写入、段落边界测试 | 阶段 10 API/UI 自动导出 |
| `PRD-PRIN-006/007` | 最终 token 保留 candidate provenance；术语、标点、对齐均追加输入/输出/rule version 审计 | `test_four_text_layers_terms_punctuation_timing_and_audit_are_persisted` | 阶段 10 Web provenance 展开 |

课程 importer 覆盖 YAML 手工、TXT/Markdown/CSV、PPTX/PDF/讲义/教科书和历史已确认来源；
自动抽取只产不超过 0.3 的建议。滚动上下文测试固定最多三个已确认句段和课程/章节/语言/
近期频率 top-K，明确为 bias-only，禁止把整堂历史或无声学支持热词注入正文。

## 阶段 10 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-CLS-001/005` | 持久全局+逐片段 checkpoint，MOSS 后动态片段，自动导出最后运行；pause/resume/cancel/recover/retry segment | `test_classroom_pipeline.py` 的两片段 19 checkpoint、可重入、恢复、抢占和导出顺序测试 | 阶段 12 真实 90 分钟权重 E2E |
| `PRD-ARCH-002/PRIV-001` | `/api/v1`、bearer+CSRF、UUID 资源和受控下载；课堂与 IBus UI/状态分离 | `test_api_v1.py` 完整 OpenAPI/safety/upload/download 合同 | 阶段 11 实时 IBus daemon |
| `PRD-TEXT-001/002` | 波形+speaker/language 轨、四层文本、700 ms 乐观锁保存、候选/provenance、撤销/重做与审计 | API 编辑/冲突/采用/审计集成测试；Vitest/Playwright 工作台流程 | 阶段 12 人工 gold UX 验收 |
| `PRD-SPK-001` | job-local speaker display name API/UI，不修改原 diarization | speaker rename 与 decision event 集成测试 | 阶段 12 多说话人真实课堂 |
| `PRD-MODE-003/004` | 上传的自动/手动与四种准确率模式原样持久化；自动 profile 只接受 completed benchmark | 作业 options、profile benchmark gate 和模型 UI 测试 | 阶段 12 生成本机排名 |
| `PRD-QUAL-003/PRIN-006/007` | transcript/candidate/token 返回质量原因、模型 revision、绝对 sample 和逐 token provenance | API token provenance、候选采用、低置信筛选和 UI 展开测试 | 阶段 12 真实模型质量阈值 |

阶段 10 不把 SSE 或前端状态当作恢复真相，也不在 API/React 中复制选模、术语、标点或时间算法。
完整接口与页面合同见 `docs/classroom-api-webui.md`。

## 阶段 11 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-IBUS-001/003` | 薄 PyGObject IBus engine；F9 hold/toggle、Esc、数字候选；稳定/下划线尾部 preedit、lookup、单次最终 commit；错误后普通键透传 | `test_stage11_dictation.py` 的 controller/engine 状态、candidate、cancel/error 测试；架构 import 门 | 阶段 12 在已安装应用中记录原生 IBus 事件 |
| `PRD-IBUS-002` | zh/ja/en 强制与 auto 双窗口 LID；主 ASR+FireRedLID 单源/双源融合；日语英文术语保护 | manual/auto/stable-boundary/FireRed-only/terminology 路由测试；FireRed `lid_pcm` 官方 API mock | 阶段 12 三语近讲 gold 与真实权重准确率 |
| `PRD-IBUS-004` | component XML + systemd/RPM；XDG Portal CreateSession/BindShortcuts/Activated/Deactivated；拒绝时 IBus fallback 与 0600 状态 | Portal callback/request/fallback 测试；八应用兼容合同矩阵 | 阶段 12 的 Wayland/X11、GNOME/KDE、八应用 installed-host report |
| `PRD-IBUS-005/PRIN-001/003` | 20 ms PCM、模型/semantic/hard 三层块、1.5～2.5 秒 overlap、绝对 sample+token 合并、rolling context、stable prefix | 五分钟 15,000 帧计划、300 token 无缺漏、semantic/hard/release 回归、exact-frame GStreamer 测试 | 阶段 12 用真实连续听写音频复核 WER/延迟 |
| `PRD-PRIN-004/005` | Nemotron 真 cache-aware encoder/RNNT 状态；fast/balanced；按语言双驻留或 core-owned hot-switch accuracy；batch-only final 的临时 PCM 桥；priority-0 same-UID GPU lease | 两连续 chunk cache/predictor 复用、PCM→batch 绝对时间、resident 热切换、确认模式隔离、lease accuracy action/幂等和课堂安全边界测试 | 阶段 12 本机 RTX 4070 VRAM、抢占和延迟测量 |
| `PRD-ARCH-002/PRIV-001/002/003` | IBus 与课堂运行时/API 分离；PipeWire PCM 默认只驻内存；控制/status/lease socket/file 受限；无云端回退 | 麦克风断开/queue/worker/socket 无 commit，same-UID/0600，API 认证与 portal 安全读取测试 | 阶段 12 网络 namespace + 实际 PipeWire/Portal 故障注入 |

阶段 11 的生产 worker 路径不会把累计整段 `transcribe` 冒充流式缓存：Nemotron 每个 open stream
保存 attention、convolution、cache length、previous hypothesis 和 predictor output；fast flush 仅
补齐尾块。应用矩阵把自动 bridge 合同和真实 installed-host 结果分开，未运行的桌面组合不会被标记
为实机通过。

## 阶段 12 已落地证据

| 需求 | 实现 | 当前自动证据 | 尚未完成的运行时范围 |
|---|---|---|---|
| `PRD-PRIN-008/MODE-006` | 私有 gold schema/loader、六组独立校准/排名、revision/manifest 失效、精确 profile apply/rollback | `tests/unit/test_benchmark.py`、registry/API benchmark gate | 中日英真实 gold 和生产 checkpoint 排名 |
| `PRD-QUAL-003/004/006/007` | raw/normalized 正文、术语/实体、标点、安全、时间、speaker、性能和 provenance 硬门 | 指标/runner/acceptance 单元测试、六个固定历史回归 | 90 分钟真实课堂和实际 CER/WER 阈值 |
| `PRD-CLS-001/002/003` | production coverage、90 分钟、多 speaker、code-switch/静音/音乐/中断/overlap 证据门 | fail-closed acceptance 合同 | 私人课堂数据、真实模型/硬件执行 |
| `PRD-IBUS-001/002/003/004/005` | 5 分钟、延迟 percentile、抢占和 GNOME/KDE/X11/八应用矩阵必须提供结构化实机证据 | 5 分钟 token 自动 guard、桌面 evidence merge 测试 | 完整实机交互矩阵和真实听写 gold |
| `PRD-PRIN-004/005` | 11 worker 同一十 case 矩阵、固定 revision、进程退出 VRAM 容差；真实证据不能用 bool 自证 | worker lifecycle/protocol tests 和 evidence validator | 全部已安装权重的空/静音/极短/超长/Unicode/OOM 实测 |

当前 Phase 12 acceptance 明确为 `incomplete`。这不是测试替身失败，而是私人数据、模型权重、
可用 CUDA 驱动和多桌面主机证据不存在；系统据此禁止把 bootstrap 排名改写为 local gold。

## 阶段 13 发布加固证据

| 范围 | 已落地控制 | 自动证据 | 必须由外部环境补齐 |
|---|---|---|---|
| 冻结供应链 | 20 模型完整 commit/manifest/license、bundle SHA 索引、人工 selection、独立锁定生成器/离线 verifier、16 个 uv lock、pnpm lock、host/redirect/hash/size 限制、两步条款确认 | manifest-tool 全量/攻击/双 clean 测试、bundle loader、model manager/download/license/environment/release-check 篡改测试 | gated 上游账号实际授权（重新生成时适用） |
| 生产运行绑定 | API runtime 使用 `ProductionStageRunner`；创建 Job 前 preflight；13 checkpoint 和本机 calibration/profile 真正进入执行 | production runner、pipeline、API、local-profile/calibration tests；architecture gate | 固定生产 checkpoint 与私有 gold 的实测指标 |
| IBus 常驻生命周期 | core supervisor、VRAM budget、frozen env、Bubblewrap、VAD/LID CPU、严格 resident manifest、idle-boundary profile 路由 | resident supervisor/manifest/router、dictation、scheduler tests | 本机 RTX 4070 的真实显存、延迟与抢占数据 |
| Fedora 交付 | RPM/spec、3 user units、IBus component、desktop/icon、doctor、installed-tree bundle validator、用户数据保留；RPM 不含权重/凭证/cache | 不同bundle双版本RPM lifecycle、21 JSON source/installed逐字节比对、doctor/static/release checks | 干净 Fedora SELinux Enforcing 正常安装/升级/卸载与完整桌面矩阵 |
| 发布授权 | `NOASSERTION` 源码/自制图标 inventory 强制阻塞签名 | `source_license=false` fail-closed gate | 版权方选择并提供 SPDX 许可证后更新 spec/inventory/NOTICE |
| 发布阻塞 waiver | 规范化独立工件；release manifest 固定路径/SHA；版本、facts、registry、三项 gate 和四项免责声明精确绑定；raw/effective 状态分离 | waiver 缺失/symlink/SHA/canonical/version/registry/额外 gate/ack/scope 攻击测试；source/RPM installed-tree 字节与篡改回归 | waiver 不补许可证、私有 gold、性能或桌面实机证据，原始风险继续公开 |

阶段 13 没有把 waiver 等同于原始证据通过。机器清单中的十个发布门仍只有七个原始值为 true；其余
三个保持 false，但精确 waiver 使 `effective_checks` 全部为 true，并以独立状态
`ready_with_waivers` 放行自动流程。无 waiver 的真正全绿状态仍只叫 `ready`。
