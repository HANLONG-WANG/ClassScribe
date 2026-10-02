# ClassScribe：MAI 在线转写、音频剪辑与单句回听开发计划

> 深度：标准；仅规划，未修改业务代码，也未调用真实收费 API。
> Evidence verified at commit f55da7ee202e106c7b1fbde91c6aa85c3f097e3a；GitNexus 索引与 HEAD 一致，runner 1.6.12，未刷新；动态调用以 Serena 和源码交叉验证。
> Evidence provenance schema 2；global dirty digest 0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd；cited-path manifest 31 个排序条目；仅排除本计划路径。

## 目标（§1）

支持课堂文件选择本地模型或 MAI；支持导入后选择一个连续音频范围、试听、剪辑并转写；点击转录句段立即回听且到句尾停止。此次不适配 ibus 实时转写。

## 现状（§2–3）

- [verified] `frontend/src/pages/TranscriptPage.tsx:31` 已有 WaveSurfer 波形、播放/暂停、跳转和倍速；`:307` 的 `seek` 只执行 `setTime(start_sample / 16000)`，点击句段尚无自动播放和句尾停止；`frontend/tests/TranscriptPage.test.tsx:29` 也只验证定位行为。
- [verified] `frontend/src/pages/UploadPage.tsx:127` 与 `frontend/src/batchImports.ts:126` 把导入直接串到建任务；`backend/classscribe/api/schemas.py:21` 的 `JobCreate` 没有在线提供方或剪辑参数。
- [verified] `backend/classscribe/classroom/production.py:211` 强制检查本地 VAD/MOSS/ASR；`backend/classscribe/classroom/pipeline.py:224` 固定阶段链，`:521` 仅在 MOSS 完成后生成句段检查点。MAI 需要自己的阶段计划。
- [verified] `backend/classscribe/timeline/__init__.py:1` 规定持久化时间为 16 kHz 整数采样点；媒体播放指向当前 Recording 的源文件（`backend/classscribe/api/service.py:283`）。当前输入上限为 90 分钟、8 GiB（`backend/classscribe/audio/media.py:28`）。
- [verified] `backend/classscribe/config.py:42`、`config/product-contract.v1.json` 及其 schema 固定离线推理；现有契约测试也固定此约束。须随本次产品需求一起更新，而非只在界面增加按钮。

## 调查结论（§4–5）

- [graph] `impact(create_job, upstream)` 为 LOW，直接调用方是 `api/routes.py` 的建任务路由；`impact(Waveform, upstream)` 为 LOW，直接调用方是 `TranscriptPage`；`impact(run_checkpoint, upstream)` 为 LOW，直接调用方是 `ClassroomPipeline.run_next`。这些调用方均纳入回归。
- [verified] `impact(preflight)` 返回 UNKNOWN，`trace(create_job → ProductionStageRunner.preflight)` 无路径；源码 `pipeline.py:270` 的 `getattr` 调用补足此边。不能据此认为无影响；Serena 也确认了预检测试引用。
- [verified] `pdg_query(run_checkpoint, controls)` 返回 19 条完整控制边；结合 `backend/classscribe/jobs/state_machine.py:294` 确认：先落检查点运行状态，再在事务中执行 operation，异常另开事务记录失败。在线 HTTP 不能直接放进这个长事务。
- [verified] 参考 `tmp/MAI-Lecture-Studio-v1.0.0/mai-lecture-studio/mai_studio/core.py:84,288` 的请求定义、流式 multipart、TLS、代理、取消机制；参考 `mai_studio/audio.py:170` 的原文件裁切与解码预热。它未被本仓库图/LSP覆盖，已直接核对源码。
- [verified] 已用最小输入复现 `core.py:184` 的丢词问题：原文“今日は晴れです。”，第二个词缺时间戳时，生成字幕只剩“今日は”。因此响应适配器重新实现，不能照搬 `make_cues`；此结论来自合成响应，未对真实服务作失败推断。
- [verified] 微软文档与参考项目一致：MAI-Transcribe-2 走 Speech REST `2025-10-15`，使用 `enhancedMode`、word 时间戳、术语和可选说话人区分；以当前官方合同为准。[官方说明](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe)

## 设计与改动（§6）

1. **提供方和配置**：为 `JobCreate` 增加 `provider=local|azure_mai`，旧请求默认 local；在线配置包含 endpoint、API version 和能力选项。`runtime_offline` 改为默认 true 的实际云端推理禁用开关，启用 MAI 需显式关闭；本地 worker 的无网络沙箱独立保留。同步配置校验、产品契约/schema、隐私文案与测试；不把 MAI 塞进本地权重安装/健康检查流程。
2. **凭证与提交**：Settings 新增 MAI 设置。首版支持后台环境变量和经本地鉴权 API 提交到后台内存的会话密钥；GET 只返回“已配置”。密钥不进入 AppSetting、任务 options、浏览器持久化、SSE、日志或导出；重启后会话密钥需重输。在线提交页明确展示目标服务、实际选段和上传动作，提交即授权该范围；默认仍选择本地。
3. **独立 MAI 客户端**：新增 `backend/classscribe/online/`，拆分请求合同、传输、响应适配与凭证；每次请求独立取消句柄，流式上传受限 FLAC，验证 Azure HTTPS endpoint，不跟随重定向，保留证书验证和代理支持。默认 verbatim+word；auto_mixed 不传强制 locales；词表映射 phraseList，speaker_count 映射能力须验证。连接、上传、服务等待、落盘分别报告，等待无虚构百分比。
4. **在线任务流程**：在 `api/runtime.py` 组合本地/在线 runner，`classroom/pipeline.py` 按 provider 生成检查点。在线链为校验/准备音频 → MAI请求 → 解析及质量/时轴校验 → 导出；不启动 VAD/LID/MOSS、本地二三模型或强制对齐，也不占 GPU 租约。复用 Job、队列、事件、TranscriptSegment、ASRCandidate、TokenSpan 和校对/导出；未提供的置信度保留 null，模型版本记录实际已知信息，不伪造权重 revision。
5. **可恢复的外部请求**：新增持久化请求 attempt/结果凭据（迁移），包括 job、请求指纹、音频哈希、请求状态、服务 request-id、响应产物及错误类别。扩展检查点执行入口为短事务准备并记录发送意图 → 事务外 HTTP → 私有结果原子落盘 → 短事务导入/完成；失败落盘、取消和重启均可区分。已成功响应只重做解析；已发送但结果未知标记需人工重试，绝不当成普通 checkpoint 自动再次收费调用。
6. **剪辑与时间轴**：新增 `POST /recordings/{id}/clips`，输入 16 kHz 坐标 `start_sample/end_sample` 和幂等键；后端校验 `0 ≤ start < end ≤ duration`，从受控源文件准确裁切并移除元数据，原文件不变。生成新的 Recording，新增 parent_recording_id/source_start_sample/source_end_sample（旧记录 nullable），保存实际解码时长和哈希；处理用 job/recording 专属临时目录，失败/取消清理，提交文件与 DB 保持可恢复一致性。
7. **剪辑界面及坐标约定**：导入后允许“整段转写”或“选择范围”，用波形 A/B 拖动和精确时间输入，支持选段试听与重设。每个片段独立建任务，能选本地或 MAI；更新 batchImports 使媒体导入不强制立即转写。派生 Recording 的转录/播放/字幕统一从片段 0 开始；原录音定位为 `source_start_sample + clip_sample`，只换算一次并在 UI/JSON 显示来源；首版不做多段拼接。
8. **响应与回听**：完整 phrase/全文为文本真值，词时间戳只作对齐证据；部分缺失时降级为保留全文的 phrase 时段，全部缺失时保留文本、标注不可精确回听/生成定时字幕，禁止伪造逐词对齐。校验非有限数、负数、倒序、越界、重复和 speaker/language 变化；中日文标点不靠简单 token 拼接重建。扩展 TranscriptPage 的 `seek` 为受控单句播放，到 end_sample 停止；重复点击重播，换句替换旧停止边界，暂停/拖动/换任务清理边界；支持键盘和加载失败提示，长录音先加载后播放。

## 实施顺序（§7）

1. **合同与基础数据**：先落 provider/配置权限、clip/attempt 数据迁移及 API 类型；旧配置/旧任务默认保持本地，给迁移和权限加测试。
2. **媒体剪辑**：实现裁切 API、幂等与来源映射，完成导入→选段→本地转写闭环；以实际音频验证裁切内容，不仅检查命令字符串。
3. **MAI 适配器**：用本地模拟 HTTP 服务验证与参考项目一致的请求，加入丢词复现和异常响应回归；配置/凭证界面此时可用。
4. **在线编排**：接入 provider 阶段计划和事务外网络请求，先跑无本地模型的模拟全链路，再验证中断/恢复/重复提交、队列控制与派生产物清理。
5. **播放与导出联调**：单句播放闭环、片段与原录音坐标展示、长录音加载及无时间戳降级；编辑/拆分/合并后回听范围仍跟随有效句段。
6. **验收与收尾**：在用户配置的资源上用短片段做真实 MAI 验证，再完成长音频/混合语言回归；更新文档和数据清理逻辑。每次函数编辑前补 impact；验证后先 detect_changes(scope=all) 检查完整结果，再 gitnexus analyze（需要时提权）；提交前不得跳过图变更分析。

## 测试策略（§8）

- **请求合同/错误**：校验 multipart、模型名、自动/指定语言、术语、word/segment、说话人开关；401/403/413/429/5xx、重定向、断网、代理、超时、取消、非法/超大 JSON；密钥从 API、数据库、日志、异常及导出均不可读回；无自动重试与隐式模型回退。
- **文本/时轴**：部分词缺时间戳的上述日语用例必须保留全文；覆盖中文标点、英文空格、静音、乱序/重叠/越界、phrase-only、完全无 timing、未知语言和缺置信度；不能把无法对齐的输出标成精确成功。
- **裁切**：WAV/FLAC/M4A 的非整秒起点、接近结尾、0 长度、反向/越界、重复请求、源文件变化及取消；用含已知脉冲/语音位置的音频对照边界内容；片段在 10:00 开始、句子在片段 5s 时，片段播放是 5s，原录音位置是 10:05。
- **编排与兼容**：无 GPU/模型可完成在线链；本地不发 MAI 请求；重启在发送前、发送后、响应落盘后、DB提交后各注入失败；结果未知不自动重发，已缓存响应不重复收费；网络等待期间取消/暂停/查询不被数据库事务锁住；旧库迁移、队列、片段重跑、导出、清理和 ibus 既有行为回归。
- **前端**：更新已存在的 `frontend/tests/TranscriptPage.test.tsx`，验证点击自动播、到尾停止、连续换句、播放拒绝、长录音首次加载、切换文本层不重建播放器；新增剪辑与在线配置测试，并用 Playwright 验证真实媒体播放而不只 mock WaveSurfer。
- **验证命令**：[verified] CI 已定义 `uv run python scripts/check_architecture.py`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv run pytest --cov --cov-report=term-missing`、`pnpm --dir frontend check`；另运行已定义的 `pnpm --dir frontend test:e2e`，按既有 Playwright 配置启动服务。当前规划阶段只运行了丢词最小复现，未声称这些检查已通过。

## 实施上下文（§11）

```json
{
  "implementation_context": {
    "task_summary": "添加MAI在线文件转写、连续范围剪辑后转写与单句点击回听；排除ibus实时适配。",
    "evidence_provenance": {
      "schema_version": 2,
      "head_commit": "f55da7ee202e106c7b1fbde91c6aa85c3f097e3a",
      "generated_plan_path": "docs/plans/2026-10-02-gitnexus-plan-mai-audio-clip-playback.md",
      "global_dirty_digest": {
        "algorithm": "sha256",
        "canonicalization": "gitnexus-evidence-provenance-v2 NUL-framed UTF-8 records",
        "value": "0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd"
      },
      "cited_path_manifest": [
        {
          "path": ".github/workflows/ci.yml",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:afdd35c0a52cfd470edf12abe7692340826f459d76ae1a67ecd287c6d840b56a",
          "index_digest": "sha256:afdd35c0a52cfd470edf12abe7692340826f459d76ae1a67ecd287c6d840b56a",
          "worktree_digest": "sha256:afdd35c0a52cfd470edf12abe7692340826f459d76ae1a67ecd287c6d840b56a",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/api/routes.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:2cf33e4cda260f5b681b029f571cbeaf81e6f3e94908f801af481bdb50f72895",
          "index_digest": "sha256:2cf33e4cda260f5b681b029f571cbeaf81e6f3e94908f801af481bdb50f72895",
          "worktree_digest": "sha256:2cf33e4cda260f5b681b029f571cbeaf81e6f3e94908f801af481bdb50f72895",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/api/runtime.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:91aed96507133f7baa977190330ea06c0b2c36cebbf9e28eb6a3c74b359ecc48",
          "index_digest": "sha256:91aed96507133f7baa977190330ea06c0b2c36cebbf9e28eb6a3c74b359ecc48",
          "worktree_digest": "sha256:91aed96507133f7baa977190330ea06c0b2c36cebbf9e28eb6a3c74b359ecc48",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/api/schemas.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:7a5689ac6f6af7e80dae7c76d17d9cd1ee054f1c38d752f4bc660255292f45a5",
          "index_digest": "sha256:7a5689ac6f6af7e80dae7c76d17d9cd1ee054f1c38d752f4bc660255292f45a5",
          "worktree_digest": "sha256:7a5689ac6f6af7e80dae7c76d17d9cd1ee054f1c38d752f4bc660255292f45a5",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/api/service.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:a0d689d0b310b24ecef5b0715a9b96abd11ebdfcf9f119d342133565cc999f88",
          "index_digest": "sha256:a0d689d0b310b24ecef5b0715a9b96abd11ebdfcf9f119d342133565cc999f88",
          "worktree_digest": "sha256:a0d689d0b310b24ecef5b0715a9b96abd11ebdfcf9f119d342133565cc999f88",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/audio/media.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:37f602fc53ee603644beb22e9fa15d137839b9ffc4d54706c63e120571f46bf4",
          "index_digest": "sha256:37f602fc53ee603644beb22e9fa15d137839b9ffc4d54706c63e120571f46bf4",
          "worktree_digest": "sha256:37f602fc53ee603644beb22e9fa15d137839b9ffc4d54706c63e120571f46bf4",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/classroom/pipeline.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:1e69748459edd236472d24a0b7ceb0aa4f9ad84634f13291789e511b07a9b52e",
          "index_digest": "sha256:1e69748459edd236472d24a0b7ceb0aa4f9ad84634f13291789e511b07a9b52e",
          "worktree_digest": "sha256:1e69748459edd236472d24a0b7ceb0aa4f9ad84634f13291789e511b07a9b52e",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/classroom/production.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:3a7a718d3a365f118a633123822f679c884a4f84ce18615dac3ef71f9125c839",
          "index_digest": "sha256:3a7a718d3a365f118a633123822f679c884a4f84ce18615dac3ef71f9125c839",
          "worktree_digest": "sha256:3a7a718d3a365f118a633123822f679c884a4f84ce18615dac3ef71f9125c839",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/config.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:2be0b0f92c9c6c961bb52cc577855e40aa4a9333c84c63345a255e0e7ec024de",
          "index_digest": "sha256:2be0b0f92c9c6c961bb52cc577855e40aa4a9333c84c63345a255e0e7ec024de",
          "worktree_digest": "sha256:2be0b0f92c9c6c961bb52cc577855e40aa4a9333c84c63345a255e0e7ec024de",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/db/models.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:b72ee8dbf86c02a2ea5ed7968baa2cb4e9211925c4612332a56d963a7288d14c",
          "index_digest": "sha256:b72ee8dbf86c02a2ea5ed7968baa2cb4e9211925c4612332a56d963a7288d14c",
          "worktree_digest": "sha256:b72ee8dbf86c02a2ea5ed7968baa2cb4e9211925c4612332a56d963a7288d14c",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/jobs/state_machine.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:bb9c01babf62094d75dcee14741677b21d1cf8f39b17255280dd7051d1635a05",
          "index_digest": "sha256:bb9c01babf62094d75dcee14741677b21d1cf8f39b17255280dd7051d1635a05",
          "worktree_digest": "sha256:bb9c01babf62094d75dcee14741677b21d1cf8f39b17255280dd7051d1635a05",
          "untracked_digest": "absent"
        },
        {
          "path": "backend/classscribe/timeline/__init__.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:57606b666c89ca4702461e62b9f5c1c30027fdca21bc0266d59b050de231703a",
          "index_digest": "sha256:57606b666c89ca4702461e62b9f5c1c30027fdca21bc0266d59b050de231703a",
          "worktree_digest": "sha256:57606b666c89ca4702461e62b9f5c1c30027fdca21bc0266d59b050de231703a",
          "untracked_digest": "absent"
        },
        {
          "path": "config/default.yaml",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:59949f738b8ece04d70247e4b46328ce743fa1038c763faec5ffadcaf7f2c7df",
          "index_digest": "sha256:59949f738b8ece04d70247e4b46328ce743fa1038c763faec5ffadcaf7f2c7df",
          "worktree_digest": "sha256:59949f738b8ece04d70247e4b46328ce743fa1038c763faec5ffadcaf7f2c7df",
          "untracked_digest": "absent"
        },
        {
          "path": "config/product-contract.v1.json",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:aadc7ac3d0d74e371aa80434d8bf36e1e10150ab513e52cd9baf2c1226eb2ab8",
          "index_digest": "sha256:aadc7ac3d0d74e371aa80434d8bf36e1e10150ab513e52cd9baf2c1226eb2ab8",
          "worktree_digest": "sha256:aadc7ac3d0d74e371aa80434d8bf36e1e10150ab513e52cd9baf2c1226eb2ab8",
          "untracked_digest": "absent"
        },
        {
          "path": "docs/security.md",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:955ef604306f13b70c3e7f16efdad9d399f60587f645b2ed70c763debf7fc4df",
          "index_digest": "sha256:955ef604306f13b70c3e7f16efdad9d399f60587f645b2ed70c763debf7fc4df",
          "worktree_digest": "sha256:955ef604306f13b70c3e7f16efdad9d399f60587f645b2ed70c763debf7fc4df",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/package.json",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:50eba95674e0b21535255f291001bb3e661f3901ebb6f49d3da582b43d00e477",
          "index_digest": "sha256:50eba95674e0b21535255f291001bb3e661f3901ebb6f49d3da582b43d00e477",
          "worktree_digest": "sha256:50eba95674e0b21535255f291001bb3e661f3901ebb6f49d3da582b43d00e477",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/src/batchImports.ts",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:c0880e1fe640a59a867b8b3a6236154d2fa8086702bc3b7efe7f09c2a81e5499",
          "index_digest": "sha256:c0880e1fe640a59a867b8b3a6236154d2fa8086702bc3b7efe7f09c2a81e5499",
          "worktree_digest": "sha256:c0880e1fe640a59a867b8b3a6236154d2fa8086702bc3b7efe7f09c2a81e5499",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/src/pages/SettingsPage.tsx",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:6326439a98cb716dd799ef7d223509abb8a853dd3109749d0719f7815c7f93e2",
          "index_digest": "sha256:6326439a98cb716dd799ef7d223509abb8a853dd3109749d0719f7815c7f93e2",
          "worktree_digest": "sha256:6326439a98cb716dd799ef7d223509abb8a853dd3109749d0719f7815c7f93e2",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/src/pages/TranscriptPage.tsx",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:f763f6ee60e67e46fbfadbf8c59b8454a27a635a78f8a073e29da99fa20a530d",
          "index_digest": "sha256:f763f6ee60e67e46fbfadbf8c59b8454a27a635a78f8a073e29da99fa20a530d",
          "worktree_digest": "sha256:f763f6ee60e67e46fbfadbf8c59b8454a27a635a78f8a073e29da99fa20a530d",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/src/pages/UploadPage.tsx",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:e75f9cb5e49948f965ddfa3b288bb39ea31f13dc788b3e303f900ec59e67b75b",
          "index_digest": "sha256:e75f9cb5e49948f965ddfa3b288bb39ea31f13dc788b3e303f900ec59e67b75b",
          "worktree_digest": "sha256:e75f9cb5e49948f965ddfa3b288bb39ea31f13dc788b3e303f900ec59e67b75b",
          "untracked_digest": "absent"
        },
        {
          "path": "frontend/tests/TranscriptPage.test.tsx",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:a49dff146e8a9d826d840197b716530624797db5df5cd3e90b13e100747488bd",
          "index_digest": "sha256:a49dff146e8a9d826d840197b716530624797db5df5cd3e90b13e100747488bd",
          "worktree_digest": "sha256:a49dff146e8a9d826d840197b716530624797db5df5cd3e90b13e100747488bd",
          "untracked_digest": "absent"
        },
        {
          "path": "packaging/systemd/classscribe-core.service",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:953fc45ea4430752c26f8c3a6fec411e8739963516956d443ff31c943a639310",
          "index_digest": "sha256:953fc45ea4430752c26f8c3a6fec411e8739963516956d443ff31c943a639310",
          "worktree_digest": "sha256:953fc45ea4430752c26f8c3a6fec411e8739963516956d443ff31c943a639310",
          "untracked_digest": "absent"
        },
        {
          "path": "protocol/schema/v1/product-contract.schema.json",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:1c6009e1645c600cf2f249950dabd0e44f4e0467a9dc65868ddc61b82b5468e2",
          "index_digest": "sha256:1c6009e1645c600cf2f249950dabd0e44f4e0467a9dc65868ddc61b82b5468e2",
          "worktree_digest": "sha256:1c6009e1645c600cf2f249950dabd0e44f4e0467a9dc65868ddc61b82b5468e2",
          "untracked_digest": "absent"
        },
        {
          "path": "pyproject.toml",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:9f2ddec87110532b9062315ad5a219af626b2cc7654d9f25ebaa7ebd3b215b64",
          "index_digest": "sha256:9f2ddec87110532b9062315ad5a219af626b2cc7654d9f25ebaa7ebd3b215b64",
          "worktree_digest": "sha256:9f2ddec87110532b9062315ad5a219af626b2cc7654d9f25ebaa7ebd3b215b64",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/contracts/test_stage0_product_contract.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:d43e67e6dd3d7046f5af7d5be3b92945fff89ea4007ffdca1c978fcfaac6e75a",
          "index_digest": "sha256:d43e67e6dd3d7046f5af7d5be3b92945fff89ea4007ffdca1c978fcfaac6e75a",
          "worktree_digest": "sha256:d43e67e6dd3d7046f5af7d5be3b92945fff89ea4007ffdca1c978fcfaac6e75a",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/unit/test_audio_pipeline.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:ea4f64b232e1fe11c7bbcad83c8be2bace19ca01c25d5c60419380d2de323a0d",
          "index_digest": "sha256:ea4f64b232e1fe11c7bbcad83c8be2bace19ca01c25d5c60419380d2de323a0d",
          "worktree_digest": "sha256:ea4f64b232e1fe11c7bbcad83c8be2bace19ca01c25d5c60419380d2de323a0d",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/unit/test_config.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:645c4053626aac5515a1cf599d27ae79043ed44b4fff95a7eeaa5d121accf8f5",
          "index_digest": "sha256:645c4053626aac5515a1cf599d27ae79043ed44b4fff95a7eeaa5d121accf8f5",
          "worktree_digest": "sha256:645c4053626aac5515a1cf599d27ae79043ed44b4fff95a7eeaa5d121accf8f5",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/unit/test_job_state_machine.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:8a31b7baa70133ea6e3777ce9fe82a30cd57444e6c21fae88d40f9edc330d720",
          "index_digest": "sha256:8a31b7baa70133ea6e3777ce9fe82a30cd57444e6c21fae88d40f9edc330d720",
          "worktree_digest": "sha256:8a31b7baa70133ea6e3777ce9fe82a30cd57444e6c21fae88d40f9edc330d720",
          "untracked_digest": "absent"
        },
        {
          "path": "tests/unit/test_production_runner.py",
          "object_kind": {
            "head": "regular",
            "index": "regular",
            "worktree": "regular",
            "untracked": "absent"
          },
          "state": "clean",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "sha256:8c9f7c13054a5d3ab06356462063130efc22a1a0dc940c941a124aa5d5571d26",
          "index_digest": "sha256:8c9f7c13054a5d3ab06356462063130efc22a1a0dc940c941a124aa5d5571d26",
          "worktree_digest": "sha256:8c9f7c13054a5d3ab06356462063130efc22a1a0dc940c941a124aa5d5571d26",
          "untracked_digest": "absent"
        },
        {
          "path": "tmp/MAI-Lecture-Studio-v1.0.0/mai-lecture-studio/mai_studio/audio.py",
          "object_kind": {
            "head": "absent",
            "index": "absent",
            "worktree": "absent",
            "untracked": "regular"
          },
          "state": "untracked",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "absent",
          "index_digest": "absent",
          "worktree_digest": "absent",
          "untracked_digest": "sha256:900a249d4aae7ee76fb69522199328ec6a00b383772669967d8f4ca1381f3614"
        },
        {
          "path": "tmp/MAI-Lecture-Studio-v1.0.0/mai-lecture-studio/mai_studio/core.py",
          "object_kind": {
            "head": "absent",
            "index": "absent",
            "worktree": "absent",
            "untracked": "regular"
          },
          "state": "untracked",
          "rename_from": null,
          "rename_to": null,
          "head_digest": "absent",
          "index_digest": "absent",
          "worktree_digest": "absent",
          "untracked_digest": "sha256:2ba2047d89f26a29f42844be82375cf83548b5fe380c931e30b43f81e8ac7bda"
        }
      ]
    },
    "files_to_modify": [
      {
        "file": "backend/classscribe/api/schemas.py",
        "symbols": [
          "JobCreate"
        ],
        "intended_change": "新增 provider 及剪辑请求/在线配置的严格 schema，local 为兼容默认值"
      },
      {
        "file": "backend/classscribe/api/service.py",
        "symbols": [
          "ClassScribeService/create_job",
          "ClassScribeService/update_settings"
        ],
        "intended_change": "提供方预检、剪辑事务与幂等；凭证另设不持久化入口，不借用普通 settings JSON"
      },
      {
        "file": "backend/classscribe/api/routes.py",
        "symbols": [],
        "intended_change": "新增受现有 loopback/auth/CSRF 保护的 clip 与凭证路由；GET不返回key"
      },
      {
        "file": "backend/classscribe/api/runtime.py",
        "symbols": [
          "build_default_service"
        ],
        "intended_change": "注入本地/在线 runner 路由与后台凭证实例，启动时不发送云端请求"
      },
      {
        "file": "backend/classscribe/classroom/pipeline.py",
        "symbols": [
          "ClassroomPipeline/preflight",
          "ClassroomPipeline/initialize",
          "ClassroomPipeline/run_next",
          "ClassroomPipeline/_ensure_segment_checkpoints"
        ],
        "intended_change": "provider 阶段计划；在线取消/恢复与无GPU执行；保留本地检查点和队列语义"
      },
      {
        "file": "backend/classscribe/jobs/state_machine.py",
        "symbols": [
          "JobStateMachine/run_checkpoint"
        ],
        "intended_change": "新增事务外外部操作入口，复用 begin/complete/fail 状态转换；现有本地 run_checkpoint 语义不扩大"
      },
      {
        "file": "backend/classscribe/db/models.py",
        "symbols": [
          "Recording",
          "Job",
          "TranscriptSegment",
          "ASRCandidate"
        ],
        "intended_change": "Recording来源字段与新在线请求attempt模型；新增对应 Alembic migration，旧任务provider缺省local"
      },
      {
        "file": "backend/classscribe/audio/clips.py (new)",
        "symbols": [],
        "intended_change": "从受控Recording源精确裁切、校验实际解码样本、专属临时文件清理、来源坐标，复用现有FFmpeg路径约束"
      },
      {
        "file": "backend/classscribe/online/mai.py (new)",
        "symbols": [],
        "intended_change": "MAI definition/HTTPS流式传输/响应校验；按请求隔离取消、超时、代理和脱敏"
      },
      {
        "file": "backend/classscribe/online/runner.py (new)",
        "symbols": [],
        "intended_change": "在线检查点、发送意图与响应缓存、幂等导入现有segment/candidate/token模型及兼容导出"
      },
      {
        "file": "backend/classscribe/online/credentials.py (new)",
        "symbols": [],
        "intended_change": "后台环境/会话内存凭证；只返回configured状态；进程结束释放会话凭证"
      },
      {
        "file": "backend/classscribe/config.py",
        "symbols": [
          "PrivacyConfig"
        ],
        "intended_change": "runtime_offline默认true但允许显式false；配合online配置校验；本地worker沙箱不随该开关开放网络"
      },
      {
        "file": "frontend/src/pages/UploadPage.tsx",
        "symbols": [
          "UploadPage"
        ],
        "intended_change": "导入后试听/剪辑入口、provider选择、实际云端上传范围说明；本地默认"
      },
      {
        "file": "frontend/src/batchImports.ts",
        "symbols": [
          "drain"
        ],
        "intended_change": "分离媒体导入与建任务，保存每文件/片段选项和稳定幂等键；恢复不重复裁切/建任务"
      },
      {
        "file": "frontend/src/pages/SettingsPage.tsx",
        "symbols": [
          "SettingsPage"
        ],
        "intended_change": "endpoint/API版本/会话密钥配置，密钥无持久化且提交后清空输入"
      },
      {
        "file": "frontend/src/pages/TranscriptPage.tsx",
        "symbols": [
          "Waveform",
          "TranscriptPage/seek"
        ],
        "intended_change": "受控区间播放、到尾停止、来源映射、无时间戳降级、长录音待加载动作"
      },
      {
        "file": "frontend/src/components/AudioClipEditor.tsx (new)",
        "symbols": [],
        "intended_change": "波形A/B区域、时间输入、试听，复用wavesurfer；不先抽象整个播放器系统"
      },
      {
        "file": "frontend/src/api.ts",
        "symbols": [],
        "intended_change": "同步新provider/clip/attempt API类型（执行时定点读取现有声明）"
      },
      {
        "file": "config/default.yaml",
        "symbols": [],
        "intended_change": "保留离线默认，添加MAI非秘密配置示例"
      },
      {
        "file": "config/product-contract.v1.json + protocol/schema/v1/product-contract.schema.json",
        "symbols": [],
        "intended_change": "契约增加显式用户发起在线课堂转写；保留无遥测及ibus本地约束，同步版本/测试"
      },
      {
        "file": "docs/security.md",
        "symbols": [],
        "intended_change": "将纯离线推理表述改为默认离线、显式MAI上传；描述凭证/取消/不确定结果/数据清理"
      }
    ],
    "tests": [
      {
        "file": "tests/unit/test_mai_client.py (new)",
        "scenarios": [
          "MAI-Transcribe-2定义与语言/术语/word时间戳的精确请求快照",
          "partial word timing => 保留phrase全文，不复制参考make_cues丢词",
          "phrase-only/无timing/非法数值/异常响应与空音频",
          "key不出现在日志、事件、数据库、导出和错误详情"
        ]
      },
      {
        "file": "tests/integration/test_mai_transport.py (new)",
        "scenarios": [
          "本地HTTP测试服务器校验multipart字段、音频哈希和固定文件名",
          "401/403/429/5xx/redirect/slow upload/timeout/cancel不自动重试",
          "生产endpoint拒绝非Azure/非HTTPS；loopback仅测试依赖注入开放"
        ]
      },
      {
        "file": "tests/integration/test_online_classroom.py (new)",
        "scenarios": [
          "零已安装模型完成在线转录和导出",
          "发送前、发送中、响应后、持久化后崩溃恢复不重复收费请求",
          "网络等待中其他事务可查询/暂停/取消；取消后迟到响应不覆盖终态",
          "数据库提交前失败可从原子保存的response重做导入",
          "缺失timing保留JSON/Markdown，定时字幕不伪造，显示明确导出缺项/警告"
        ]
      },
      {
        "file": "tests/unit/test_audio_clips.py (new)",
        "scenarios": [
          "范围类型拒绝bool/浮点非法值/反向/越界",
          "同一幂等键同参数复用，不同参数冲突",
          "parent范围来源只累加一次，首版拒绝嵌套片段或先统一解析根Recording"
        ]
      },
      {
        "file": "tests/integration/test_audio_clips.py (new)",
        "scenarios": [
          "真实WAV/FLAC/M4A非整秒裁切和首尾语音不丢失",
          "原音频哈希不变、实际片段时长一致",
          "取消/磁盘满/失败/并发裁切隔离临时文件并能清理孤儿产物",
          "片段使用本地与MAI相同的媒体坐标"
        ]
      },
      {
        "file": "tests/unit/test_job_state_machine.py",
        "scenarios": [
          "现有恢复/重试/暂停/取消测试回归",
          "外部操作不确定状态不能被普通自动恢复重发"
        ]
      },
      {
        "file": "tests/unit/test_production_runner.py",
        "scenarios": [
          "本地preflight仍要求正确模型；在线runner不借用本地预检"
        ]
      },
      {
        "file": "tests/unit/test_config.py",
        "scenarios": [
          "默认offline=true，显式online配置校验；原配置正常加载"
        ]
      },
      {
        "file": "tests/contracts/test_stage0_product_contract.py",
        "scenarios": [
          "云端课堂转写是显式允许的网络操作；无遥测和本地ibus不变"
        ]
      },
      {
        "file": "frontend/tests/TranscriptPage.test.tsx",
        "scenarios": [
          "点击句段立即播放且到句尾停",
          "快速换句不受前一次停止回调干扰",
          "play拒绝/媒体失败/首次加载后播放/文本层切换/筛选不漂移"
        ]
      },
      {
        "file": "frontend/tests/AudioClipEditor.test.tsx (new)",
        "scenarios": [
          "输入和拖动双向一致、越界、选段试听、取消、不改源文件"
        ]
      },
      {
        "file": "frontend/tests/MaiSettings.test.tsx (new)",
        "scenarios": [
          "保存非秘密设置、会话key一次提交后清空、无storage副作用"
        ]
      },
      {
        "file": "frontend/e2e/mai-clips-playback.spec.ts (new)",
        "scenarios": [
          "导入→选段→模拟MAI→校对→点击句段→句尾停止→导出",
          "真实浏览器验证currentTime和paused，不仅验证mock调用"
        ]
      }
    ],
    "verification_commands": [
      "uv run python scripts/check_architecture.py",
      "uv run ruff check .",
      "uv run ruff format --check .",
      "uv run mypy",
      "uv run pytest --cov --cov-report=term-missing",
      "pnpm --dir frontend check",
      "pnpm --dir frontend test:e2e",
      "GitNexus detect_changes(scope=all) before index refresh; reject partial/truncated verdict",
      "gitnexus analyze after validation (request escalation if sandbox requires)"
    ],
    "pdg_constraints": [
      {
        "description": "run_checkpoint在begin_checkpoint提交后，在另一个事务中调用operation；普通异常和CheckpointInterrupted走不同恢复分支。PDG controls total=19，源码已确认。",
        "affected_statements": [
          "backend/classscribe/jobs/state_machine.py:306",
          "backend/classscribe/jobs/state_machine.py:309",
          "backend/classscribe/jobs/state_machine.py:317",
          "backend/classscribe/jobs/state_machine.py:324"
        ],
        "implementation_consequence": "在线发送意图先提交；HTTP在事务外；结果原子保存后再导入；不确定结果不能经CheckpointInterrupted普通pending恢复自动重发。"
      }
    ],
    "assumptions": [
      "首版剪辑是单个连续范围，多片段分任务，不拼接。",
      "在线实际endpoint/区域/diarization限制以实施时官方文档及短录音实测核实；不把参考程序保护阈值当永久合同。",
      "provider新字段缺省local；旧配置runtime_offline=true仍阻止在线调用。",
      "首版会话key只存在后台内存，或从后台环境注入；重启需重输会话key。"
    ],
    "open_questions": [
      "实现前核实用户可用MAI资源返回的locale/speaker/timestamps形状，并保存脱敏测试fixture。",
      "没有时间戳时在线专用验证应允许保留文本与明确警告，JSON/MD可用，SRT/VTT标记无法生成；不可绕过校验伪造精确token。",
      "参考项目未知bug尚未全量排除；协议保留、解析重新实现，真实验证另做。"
    ],
    "avoid": [
      "不修改ibus实时识别或解除本地worker网络隔离",
      "不照搬参考make_cues：部分词无timing会丢字",
      "不把Azure Key存入AppSetting/options_json/localStorage/日志",
      "不把MAI伪装成本地可下载模型或静默fallback",
      "不在数据库长事务里上传/等待云端",
      "不对已发送但结果未知的转写自动重试",
      "不把片段坐标和原录音偏移相加两次",
      "不扩大本项目当前90分钟音频输入上限",
      "不重复全仓调查；实施前只核实证据漂移和具体待改符号impact"
    ],
    "index_refresh": "skipped: current index commit matches clean HEAD; index runner schema current; no production changes in planning",
    "graph_boundaries": [
      "preflight impact UNKNOWN; dynamic getattr at ClassroomPipeline.preflight verified from source",
      "trace service.create_job to ProductionStageRunner.preflight no_path is a static dispatch gap",
      "preflight PDG name/UID probes did not resolve a valid function; only run_checkpoint PDG used"
    ],
    "reproduction": {
      "reference_file": "tmp/MAI-Lecture-Studio-v1.0.0/mai-lecture-studio/mai_studio/core.py",
      "function": "make_cues",
      "input": "phrase=今日は晴れです。; first word=今日は with timing; second word=晴れです。 missing timing",
      "observed": "transcript_text returns 今日は晴れです。; make_cues returns only 今日は",
      "expected": "retain phrase text and explicitly degrade timing"
    },
    "estimated_delivery": "按6个顺序里程碑交付；耗时取决于真实MAI资源与异常恢复联调，不把未执行的测试作为完成证据。"
  }
}
```

## 假设、待核实与延期项（§12）

- [assumed] “剪辑”首版按单个连续范围处理；多个范围分别建任务。整段与片段均支持 MAI，超过服务大小/时长限制时要求缩短片段，不悄悄拆成多个收费请求。仍保留本项目现有 90 分钟输入上限。
- [assumed] 当前可用资源使用参考项目的 Azure Speech 接口。区域权限、实际 diarization 时长限制、locale/speaker 响应形状和时间戳精度需以实现阶段官方合同及短片段实测核实；参考项目的 250 MB/2h 保护值不等同于所有模式的永久服务承诺。
- [inferred] 最大风险是外部请求不确定性与恢复、文本/timing 不一致，以及原音频和片段偏移重复计算；图上的 LOW 不代表这些业务风险低。首版默认无自动云端重试；持久密钥/keyring、多段拼接、自动云端分块、跨请求说话人合并、原录音坐标 SRT 快捷导出另行扩展。

## 完成标准（§13）

无需安装本地神经模型即可用 MAI 完成文件/片段转写并进入现有校对与导出；同一片段可走本地模型；原录音不被修改；点击有效定时句段自动播放并停于句尾；文本不会因缺时间戳丢失；在线操作范围明确、密钥不泄漏、恢复不自动重复收费调用；旧数据及本地/ibus 回归和上述验证通过，真实 MAI 测试结果与仍有限制如实记录。
