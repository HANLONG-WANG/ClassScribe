# Model Manifest Bundle 完整修复计划

## 1. 文档目的

本文定义 ClassScribe 模型 manifest bundle 从缺失状态修复到可发布、可安装、可审计的完整实施计划。
最终目标不是简单提交若干 JSON，而是建立一条确定性的供应链：发布工程在联网环境中从固定 Hugging
Face commit 生成并复核 manifest，应用运行时只读取随版本发布的只读 bundle，用户明确确认后按
manifest 下载模型，真实离线健康检查通过后才激活 revision。

本文中的生成和验证命令是计划新增的接口；在对应实现合并前，它们不是当前仓库已有能力。

## 2. 当前状态与问题定义

### 2.1 已有能力

当前代码已经具备以下基础：

- `config/model-registry.v1.yaml` 登记 20 个模型，包含固定 repository、40 字符 commit、worker、
  trust policy、许可证关联和 enabled 状态。
- `config/model-revisions.lock.json` 冻结模型身份与 revision。
- `config/model-licenses.v1.json` 冻结许可证披露和 gated 条款要求。
- `protocol/schema/v1/model-manifest.schema.json` 定义单模型 manifest v1。
- `ModelManager` 实现一次性确认 token、磁盘预检、staging、逐文件哈希校验、供应链审计、真实健康
  检查、原子激活、回滚和删除。
- `HuggingFaceDownloader` 只下载 manifest 指定的固定 revision 文件，并限制 HTTPS host 和 redirect。
- RPM 会整体复制 `config/`，因此未来加入 `config/model-manifests/` 后无需单独维护模型权重包。

### 2.2 缺失与矛盾

当前阻塞包括：

1. 仓库中没有任何生产单模型 manifest，也没有 bundle 索引。
2. 没有联网的发布期生成器、离线 bundle verifier 或模型文件选择策略。
3. WebUI 要求用户从文件系统上传任意 manifest JSON；安装 API 接收整份 manifest，而不是加载发布方
   冻结的 bundle。
4. CLI 没有模型 manifest 生成、校验或批量状态检查入口。
5. `release/release-manifest.v1.json` 没有把单模型 manifest 或 bundle 索引列为必须工件，发布检查也
   没有验证 registry、revision lock、license inventory 与 manifest bundle 的一一对应关系。
6. 开发进度文档声称 20 个模型的逐文件 manifest 已冻结，但实际工件不存在。
7. `ManifestFile` 要求 `kind=remote_code` 的文件必须位于 `code/`，而 downloader 同时把同一个
   `path` 用作上游路径和本地安装路径。MOSS Preview 等 worker 又按上游原始布局从模型根读取代码和
   模板。这三个合同目前互相冲突。
8. 注册表中的部分 disabled 模型没有真实推理实现，不能通过现有 load → infer → unload 健康检查；
   即使有 manifest，也不能被宣传为可安装模型。

### 2.3 修复边界

本计划负责：

- manifest/bundle 格式、生成、验证和发布；
- remote-code 文件合同修复；
- 内置 manifest 的后端加载和安装 API 接入；
- WebUI 模型安装入口改造；
- RPM、release check、测试和文档同步；
- 对 enabled 模型进行真实安装健康验收。

本计划不负责：

- 下载或提交模型权重到 Git/RPM；
- 绕过 gated 模型条款；
- 为尚无实现的 FireRedASR2-LLM、Voxtral 或 VibeVoice 补写推理 worker；
- 伪造本机 benchmark、显存或准确率结论；
- 把模型安装并入普通任务恢复或运行时自动下载。

## 3. 目标状态

完成后应具备以下目录：

```text
config/
├── model-file-selection.v1.yaml
└── model-manifests/
    └── v1/
        ├── bundle.v1.json
        ├── moss_td_0_9b.json
        ├── firered_asr2_aed.json
        ├── firered_asr2_llm.json
        ├── firered_vad.json
        ├── firered_lid.json
        ├── firered_punc.json
        ├── granite_speech_4_1_2b.json
        ├── qwen3_asr_1_7b.json
        ├── qwen3_asr_0_6b.json
        ├── qwen3_forced_aligner_0_6b.json
        ├── ark_asr_3b.json
        ├── moss_transcribe_preview_2b.json
        ├── nemotron_3_5_asr_streaming_0_6b.json
        ├── nemotron_speech_streaming_en_0_6b.json
        ├── granite_speech_5_0_turboctc_470m.json
        ├── voxtral_mini_4b_realtime_2602.json
        ├── vibevoice_asr_streaming_1_5b.json
        ├── pyannote_community_1.json
        ├── fun_asr_nano_2512.json
        └── whisper_tiny_reference.json
```

bundle 应覆盖注册表全部 20 个模型，以便供应链 inventory 完整。是否允许普通用户安装仍由注册表
`enabled`、worker 实现状态和额外安装策略共同决定，不能仅以“manifest 存在”推导为可运行。

## 4. 必须先确定的设计决策

### 4.1 Remote code 保留原始布局

采用以下方案：

- `ManifestFile.path` 同时表示上游 repository 内路径和安装 revision 内路径；
- 所有文件必须保留上游原始相对布局；
- `kind=remote_code` 可以位于任意安全相对路径，不再强制 `code/` 前缀；
- remote code 的识别依赖显式 `kind`、逐文件 SHA-256、manifest SHA-256 和安装审计；
- worker 仍只在无网络 Bubblewrap 中读取单个只读模型 revision。

这是 v1 的最小兼容修复。若必须维持 `code/` 审计目录，则应设计 manifest v2，分离
`source_path`、`audit_path` 和 `runtime_path`，并为运行时物化副本增加独立哈希；该方案不在本轮采用。

需要同步修改：

- `backend/classscribe/models/manager.py`：删除 remote-code 路径前缀限制；
- `protocol/schema/v1/model-manifest.schema.json`：文档描述明确 `path` 保留上游布局；
- `docs/model-installation.md`：删除“remote code 必须位于 code/”的陈述；
- 相关 manager/download/schema 合同测试。

### 4.2 生成器与运行时依赖隔离

`huggingface_hub` 只用于联网发布工具，不加入 core 或 worker 的生产依赖。建议新增独立工具工程：

```text
tools/model-manifest/
├── pyproject.toml
├── uv.lock
└── classscribe_manifest_tool/
    ├── __init__.py
    ├── cli.py
    ├── generator.py
    ├── selection.py
    └── verifier.py
```

根目录的薄脚本可以转发到该工具，但不能复制实现。该工具的 lock 也必须进入 release dependency lock
检查。

### 4.3 文件集合必须人工策展

生成器不得根据扩展名自动决定模型 payload。上游仓库常同时含 PyTorch、ONNX、GGUF、不同精度、
多个 checkpoint 或训练材料；盲目抓取整个仓库会造成巨大浪费或产生错误 runtime。

`config/model-file-selection.v1.yaml` 应为每个模型定义人工审核的精确 include 集合。允许 pattern 仅用于
首次发现，冻结输出必须展开成精确路径。建议格式：

```yaml
schema_version: 1
models:
  qwen3_asr_0_6b:
    include:
      - config.json
      - generation_config.json
      - preprocessor_config.json
      - tokenizer.json
      - tokenizer_config.json
      - model.safetensors.index.json
      - model-00001-of-00002.safetensors
      - model-00002-of-00002.safetensors
    kinds:
      config.json: config
      generation_config.json: config
      preprocessor_config.json: config
      tokenizer.json: tokenizer
      tokenizer_config.json: tokenizer
      model.safetensors.index.json: model
      model-00001-of-00002.safetensors: model
      model-00002-of-00002.safetensors: model
    exclude: []
```

上例文件名仅展示格式；必须以固定 revision 的真实 tree 和实际 worker 加载行为为准。

## 5. Bundle 数据合同

### 5.1 单模型 manifest

继续使用 manifest v1 的核心字段：

```json
{
  "manifest_version": 1,
  "model_id": "qwen3_asr_0_6b",
  "repository": "Qwen/Qwen3-ASR-0.6B",
  "revision": "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
  "worker": "qwen",
  "license_id": "Apache-2.0",
  "license_url": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B",
  "requires_terms_acceptance": false,
  "trust_remote_code": false,
  "estimated_download_bytes": 0,
  "installed_size_bytes": 0,
  "environment": {
    "python": "3.12",
    "runtime_backend": "qwen_asr_vllm",
    "dtype": "bfloat16",
    "worker_lock": "workers/qwen/uv.lock",
    "worker_lock_sha256": "ef7d017bad97b5f521a4c137d7fec50bfc8dfdcc454a11a6699a111ba29809ce"
  },
  "files": []
}
```

生成器必须保证：

- `revision` 是注册表和 revision lock 中相同的完整 commit；
- repository、worker、trust policy 与注册表完全一致；
- license 三元组与许可证 inventory 完全一致；
- `files` 非空、按 Unicode 路径升序排列且无重复；
- `installed_size_bytes` 等于所有文件大小之和；
- 当前 downloader 没有额外压缩/转换阶段，因此 `estimated_download_bytes` 初始也取该和；
- environment 全部是字符串，至少披露 Python、runtime backend、dtype、worker lock 路径和 lock SHA；
- JSON 使用 UTF-8、LF、排序 key、固定缩进并以单个换行结束，以产生可复现 manifest SHA。

### 5.2 Bundle 索引

新增 `protocol/schema/v1/model-manifest-bundle.schema.json`，建议索引格式：

```json
{
  "schema_version": 1,
  "registry_revision": 1,
  "facts_as_of": "2026-09-03",
  "generated_at": "2026-09-04T00:00:00Z",
  "generator": {
    "name": "classscribe-model-manifest",
    "version": "1",
    "lock_sha256": "<64 lowercase hex>"
  },
  "manifests": [
    {
      "model_id": "qwen3_asr_0_6b",
      "path": "qwen3_asr_0_6b.json",
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

`generated_at` 只记录生成批次时间，不参与文件内容选择。重新生成同一批次用于 reproducibility 验证时，
通过显式 `SOURCE_DATE_EPOCH` 固定它。

索引必须满足：

- 恰好包含 registry 的 20 个 model ID；
- 每个 ID 唯一，按 model ID 排序；
- path 是 bundle 目录内的安全单层文件名；
- SHA-256 对应 manifest 规范化后的实际字节；
- 不携带 token、用户名、本机路径、cache 路径或下载者身份。

## 6. 发布期生成流程

### 6.1 输入和凭证

生成器输入固定为：

- `config/model-registry.v1.yaml`；
- `config/model-revisions.lock.json`；
- `config/model-licenses.v1.json`；
- `config/model-file-selection.v1.yaml`；
- 每个 worker 的 `uv.lock`；
- 可选的 `HF_TOKEN`，只用于 gated repository。

凭证只能从安全环境或 0600 文件读取，只放入 Authorization header，不写入异常消息、日志、manifest、
bundle 或临时路径名。pyannote 必须先由真实用户接受上游访问条件。

### 6.2 仓库树发现

对每个模型：

1. 调用 Hugging Face repository tree API，明确传入 registry 的完整 commit revision，并递归枚举文件。
2. 拒绝 revision 未解析到同一 commit、repository 不存在或 gated 权限不足。
3. 保存发现报告到临时工作区，供发布者选择文件；发现报告不进入运行时 bundle。
4. 将 selection 中每个精确路径与 tree 对照，拒绝缺失、多余或大小无法确认的条目。

Hugging Face 的普通 `blob_id` 是 Git OID，不是项目所需的 SHA-256；LFS 项虽然可能提供 SHA-256，仍应
统一以实际下载后的文件字节复算，避免为 Git、LFS、Xet 分别实现不一致的信任路径。

### 6.3 下载与哈希

1. 使用 `mktemp` 创建 0700 临时根，不使用用户模型运行缓存作为生成目录。
2. 按 repository、完整 revision 和精确 allowlist 下载，保留上游目录结构。
3. 删除下载工具产生的 `.cache/huggingface/` 元数据，不把它列入 payload。
4. 拒绝 symlink、hardlink 异常、FIFO、device、socket、绝对路径、`..`、重复路径和大小超限。
5. 对每个普通文件流式计算 SHA-256 和实际字节数。
6. 将实际集合与 selection 精确比较，拒绝任何未声明文件或缺失文件。
7. 依据 selection 的显式映射设置 kind；未分类文件不得默认为 model，应失败并要求发布者决定。
8. 将文件列表排序并生成 manifest。
9. 清理临时目录；清理失败应报告但不得泄漏 token。

官方 `huggingface_hub` 支持以完整 revision 下载、使用 allow pattern 过滤，以及在 `local_dir` 保留原始
目录结构：<https://huggingface.co/docs/huggingface_hub/en/guides/download>。

### 6.4 确定性和双重生成

正式冻结前应在空临时目录中连续生成两次：

- 两次下载解析到相同 commit；
- 所有 payload 文件 size/SHA 相同；
- 除非显式改变 `SOURCE_DATE_EPOCH`，两次 manifest 和 bundle 字节完全相同；
- 如不一致，停止发布并输出仅包含路径和哈希差异的报告。

### 6.5 计划新增的命令

目标命令接口：

```bash
uv run --project tools/model-manifest classscribe-model-manifest discover \
  --registry config/model-registry.v1.yaml \
  --selection config/model-file-selection.v1.yaml \
  --output /tmp/classscribe-model-discovery

uv run --project tools/model-manifest classscribe-model-manifest generate \
  --registry config/model-registry.v1.yaml \
  --revisions config/model-revisions.lock.json \
  --licenses config/model-licenses.v1.json \
  --selection config/model-file-selection.v1.yaml \
  --output config/model-manifests/v1

uv run --offline --frozen --project tools/model-manifest \
  classscribe-model-manifest verify \
  --bundle config/model-manifests/v1/bundle.v1.json
```

`discover` 和 `generate` 是明确联网的发布操作；`verify` 必须能够完全离线运行。

## 7. 逐模型文件策略

### 7.1 通用 Transformers 模型

必须从 config、processor/tokenizer 配置和权重 index 推导完整闭包：

- `config.json` 以及引用的专用 config；
- tokenizer vocab/model/merges、tokenizer config、special tokens；
- processor、feature extractor、preprocessor config；
- generation config；
- 选择的一种 runtime 权重格式；
- index 的 `weight_map` 引用的全部 shard；
- config `auto_map` 和 processor 动态加载引用的所有 Python 文件；
- worker 显式打开的模板或额外资源。

不得同时包含未使用的 ONNX、TensorFlow、Flax、GGUF 或其他精度副本。

### 7.2 FireRed 家族

selection 至少覆盖 worker 当前硬检查，并继续通过真实加载确定传递依赖：

- `firered_vad`：`cmvn.ark`、`model.pth.tar`；如发布 streaming 子目录布局，必须完整保留 `VAD/`、
  `Stream-VAD/` 与 worker 期望一致的结构。
- `firered_lid`：`cmvn.ark`、`model.pth.tar`、`dict.txt`。
- `firered_asr2_aed`：`model.pth.tar`、`cmvn.ark`、`dict.txt`、`train_bpe1000.model`。
- `firered_punc`：`config.yaml`、`model.pth.tar`、`chinese-bert-wwm-ext_vocab.txt`、`out_dict` 以及
  完整 `chinese-lert-base/` 运行闭包。
- `firered_asr2_llm`：生成供应链 manifest 供 inventory 使用，但在真实 LLM worker 和目标硬件验收完成前
  保持 disabled 且安装入口不可用。

### 7.3 MOSS 与 ARK remote code

- `moss_td_0_9b`、`moss_transcribe_preview_2b`、`ark_asr_3b` 的所有 Python 动态代码、模板和自定义
  processor 文件必须保留上游布局并标为 `remote_code`。
- 解析 `auto_map`、动态 processor 引用和直接文件访问，不能只按 `.py` 扩展名猜测闭包。
- 健康检查必须在网络 namespace 被取消且 Hugging Face offline flags 强制为 1 的环境中完成。

### 7.4 Qwen

- 两个 ASR checkpoint 与 ForcedAligner 分别生成独立 manifest；
- 保留它们各自 tokenizer/processor/权重闭包；
- 不创建不存在的 `Qwen3-ASR-1.7B-JA` 上游身份；
- ForcedAligner 健康检查必须提供与短音频一致的精确参考文本和受支持语言。

### 7.5 Granite

- `granite_speech_4_1_2b` 和 `granite_speech_5_0_turboctc_470m` 独立冻结；
- 只选择 worker 实际使用的 PyTorch/Transformers 权重格式；
- TurboCTC 当前未进入 bootstrap ranking，manifest 存在不等于默认加载。

### 7.6 Nemotron

- 每个 manifest 安装目录必须最终恰好包含一个可被 worker 找到的根级 `.nemo` archive；
- 不得同时安装多个 `.nemo` 变体；
- 两个 checkpoint 独立生成 manifest，不共享或改名 archive。

### 7.7 pyannote

- `pyannote_community_1` 是 gated 模型；生成和安装均要求真实授权，不能把 token 写入 bundle；
- 必须验证 `config.yaml` 引用的全部离线组件均被包含，不能在运行时解析远程模型 ID；
- 对嵌套组件的来源和许可证作单独审计；若它们来自其他 repository，应扩展供应链合同表达多来源，不能
  将其错误归因于 Community-1 单一 repository；
- 多来源能力未实现前，不得通过手工复制嵌套 cache 伪造单仓库 manifest。

### 7.8 disabled 实验模型

- `fun_asr_nano_2512` 和 `whisper_tiny_reference` 可以生成并验证 manifest，但继续保持 disabled；
- `firered_asr2_llm`、`voxtral_mini_4b_realtime_2602`、`vibevoice_asr_streaming_1_5b` 在真实 worker
  未实现前只进入供应链 inventory，不进入普通安装操作；
- verifier 应区分 `manifest_available`、`worker_implemented`、`installable` 和 `enabled`，避免一个布尔值
  混淆四种状态。

## 8. 后端接入计划

### 8.1 Bundle loader

新增 `backend/classscribe/models/manifests.py`：

- `ManifestBundleIndex`、`ManifestBundleEntry` 数据模型；
- `load_manifest_bundle(path, registry, revisions, licenses)`；
- manifest 自身 SHA 验证；
- bundle/registry/revision/license/worker-lock 全量一致性验证；
- `manifest(model_id)` 只读查询；
- 安全路径和非 symlink 校验。

默认服务从 `resource_path("config/model-manifests/v1/bundle.v1.json")` 加载 bundle，并注入
`ClassScribeService`。缺少 bundle、SHA 不符或交叉验证失败时，服务健康状态应明确报告模型安装功能不可用；
不得静默退回用户上传任意 manifest。

### 8.2 API 合同

将普通安装接口改为：

```http
POST /api/v1/models/{model_id}/install
Content-Type: application/json

{}
```

服务依据 model ID 从只读 bundle 取 manifest，再执行现有 request/confirm 两阶段流程。响应继续包含
manifest SHA、下载量、安装量、临时空间、许可证、remote-code 变更摘要和环境披露。

建议补充：

```http
GET /api/v1/models/{model_id}/manifest
```

只返回公开 manifest 和其 SHA，不返回确认 token、HF token 或本机路径。

如果未来支持 BYOM，应使用独立的专家接口和独立策略，例如：

```http
POST /api/v1/expert/models/{model_id}/install-custom
```

不得让普通安装入口继续接受任意 manifest。

### 8.3 模型列表状态

`GET /models` 增加：

- `manifest_available`；
- `manifest_sha256`；
- `estimated_download_bytes`；
- `installed_size_bytes`；
- `worker_implemented`；
- `installable`；
- `install_block_reason`。

`installable` 至少要求：manifest 完整、worker 支持该模型 ID、依赖 lock 存在、registry policy 允许。
`enabled` 继续表示自动候选资格，两者不能互相替代。

## 9. WebUI 接入计划

修改 `frontend/src/pages/ModelsPage.tsx`：

- 删除 manifest 文件选择器和浏览器端 `JSON.parse`；
- “准备安装”直接 POST model ID；
- 显示 manifest SHA、下载大小、安装大小、remote-code 文件数和安装阻塞原因；
- `installable=false` 时禁用按钮并展示具体原因；
- `enabled=false` 但可供专家安装的模型必须先经过单独的专家启用流程；
- confirm 阶段保留一次性 token、健康录音、语言、ForcedAligner 精确文本和 gated 条款确认；
- 安装错误不得显示 HF token、Authorization header 或完整本机路径。

另需改善健康录音入口：模型页应允许上传或选择已有的合规短 WAV，避免要求用户从其他页面手工抄 UUID。
上传后先验证非空、16 kHz、mono、16-bit PCM、最长 15 秒，再用于确认。

## 10. Release、RPM 与文档

### 10.1 Release inventory

更新 `release/release-manifest.v1.json`，至少加入：

- `protocol/schema/v1/model-manifest-bundle.schema.json`；
- `config/model-file-selection.v1.yaml`；
- `config/model-manifests/v1/bundle.v1.json`；
- 20 个单模型 manifest，或由 bundle verifier 安全展开的完整列表；
- manifest tool 的 `pyproject.toml` 和 `uv.lock`；
- bundle loader 和 verifier 源码。

release check 必须从 bundle 索引解析成员并验证，不能只检查目录存在。

### 10.2 RPM

RPM 当前 `cp -a config` 已能携带 bundle。新增 RPM 测试必须验证 installed tree 中：

- bundle 索引存在且不可写；
- 20 个 manifest 齐全；
- loader 能从 `/usr/share/classscribe` 解析；
- RPM 不包含模型权重、HF token、Hugging Face cache 或生成临时目录；
- upgrade 后新 bundle 生效，用户 XDG 模型 revision 不被删除。

### 10.3 文档同步

更新：

- `docs/model-installation.md`：内置 bundle、remote-code 原始布局、生成/安装权限边界；
- `docs/user-guide.md`：用户不再选择 manifest 文件；
- `docs/openapi.md`：新的 request/response；
- `docs/security.md`：发布期生成器联网与运行期离线边界；
- `docs/known-limitations.md`：真实模型未验收前继续保留阻塞；
- `docs/ClassScribe分阶段开发进度.md`：纠正“manifest 已冻结”的不实状态，完成后再恢复为已完成；
- `docs/requirements-traceability.md`：把 bundle、生成器、verifier、RPM 和真实健康检查映射到测试证据。

## 11. 测试计划

### 11.1 单元测试

新增或扩展以下测试：

1. manifest 文件安全路径、排序、重复和 SHA/size 校验。
2. remote code 可保留安全的上游根路径，仍拒绝绝对路径、`..`、symlink 和 control filename。
3. bundle 索引 schema、manifest SHA、缺件、额外项、重复 ID 和路径逃逸。
4. registry/revision/license/trust/worker/worker-lock 任一漂移均 fail closed。
5. generator 对普通 Git、LFS 和 Xet 下载结果统一按实际字节哈希。
6. selection 精确闭包、未分类文件、index 漏 shard 和额外文件拒绝。
7. 生成两次产生字节一致结果。
8. token 不进入输出、日志或异常文本。
9. API 忽略/拒绝客户端提交的任意 manifest，并只使用 bundle 内容。
10. disabled、未实现、无 manifest 和可安装模型的状态区分。

### 11.2 集成测试

- 使用本地假 Hugging Face server 模拟固定 revision、LFS redirect、短写、超长、跨 host redirect、
  revision 漂移和 gated 403；
- 从 bundle 取 manifest完成 request → confirm → download → verify → health → active 全流程；
- manifest SHA 被篡改时在联网前失败；
- bundle 升级后展示文件 diff，失败恢复旧 active；
- worker lock 变化后旧环境不能被运行时复用；
- packaged resource root 和 source resource root 行为一致。

### 11.3 前端测试

- 不再出现 manifest 文件 input；
- installable 模型可发起预检；
- disabled/未实现模型展示阻塞原因且不能安装；
- gated 条款未接受时不能 confirm；
- ForcedAligner 缺精确文本时不能 confirm；
- 健康 WAV 上传与 UUID 回填；
- 下载量、空间、manifest SHA 和 remote-code 披露正确。

### 11.4 RPM 与发布测试

- RPM install/upgrade/erase 保留现有用户模型；
- installed-tree bundle 完整且与 source tree 哈希一致；
- release check 在删除任意一个 manifest、修改任意一个 byte 或更改 registry revision 时失败；
- bundle verifier 能在完全离线环境运行；
- release readiness 在没有真实模型验收时仍保持 blocked，不因 manifest 存在自动放行 Phase 12。

### 11.5 真实模型验收

按以下顺序降低风险：

1. `whisper_tiny_reference`：CPU 小模型，验证真实 bundle → 下载 → 离线 worker 生命周期。
2. FireRedVAD、FireRedLID：CPU 辅助模型。
3. `qwen3_asr_0_6b`：较小 ASR 与 IBus 路径。
4. 中文、日语、英语三个课堂 primary。
5. MOSS 结构、ARK 和其他 fallback。
6. ForcedAligner、FireRedPunc。
7. pyannote gated 与离线嵌套依赖。
8. Nemotron 两个 streaming checkpoint。
9. TurboCTC 和 FunASR 实验项。
10. FireRed LLM、Voxtral、VibeVoice 只有在各自真实 worker 完成后验收。

每个 enabled 模型必须在目标 Fedora 环境完成：

- bundle SHA 验证；
- 安装预检与空间披露；
- 固定 revision 下载；
- 无网络 Bubblewrap load → 对应任务 infer → unload；
- 非空、有意义且符合协议的响应；
- active revision 和 `supply-chain.json` 复验；
- 重启 core 后离线再次推理；
- 实测 VRAM/CPU 路由记录，但不将单次结果冒充正式 benchmark。

## 12. 分阶段实施顺序

### 阶段 A：合同修复

- 修复 remote-code 路径合同；
- 新增 bundle schema 和数据模型；
- 增加基础单元测试；
- 更正文档中的 `code/` 约束。

退出条件：现有测试通过，新增测试证明 remote code 能保留原始安全布局且所有路径攻击仍被拒绝。

### 阶段 B：发布工具

- 创建独立 manifest tool 工程和 frozen lock；
- 实现 discover/generate/verify；
- 创建 selection schema/loader；
- 完成 deterministic JSON、双重生成和 secret-redaction 测试。

退出条件：使用本地 fixture repository 可确定性生成 bundle，离线 verifier 能发现任意篡改。

### 阶段 C：小模型端到端

- 为 Whisper Tiny 创建真实 selection 和 manifest；
- 完成内置 bundle loader；
- 通过真实 CPU 下载和健康检查；
- 暂不开放其他模型安装。

退出条件：全新 XDG 环境中不提供用户 manifest 文件即可安装、重启、离线复验 Whisper Tiny。

### 阶段 D：API 与 WebUI

- 普通 API 改为按 ID 读取 bundle；
- 模型列表增加 manifest/installability 状态；
- WebUI 移除文件选择器；
- 增加健康 WAV 上传/选择；
- 保持二次确认和条款接受。

退出条件：浏览器端不能替换发布 manifest，普通用户可以完成受支持模型安装。

### 阶段 E：生产模型 bundle

- 按逐模型策略生成全部 20 个 manifest；
- 对 14 个当前实际使用模型执行真实健康验收；
- 对 TurboCTC 和 disabled 项记录准确状态；
- 解决 pyannote 多来源闭包后再标记其 manifest 完整。

退出条件：registry、locks、licenses、selection、bundle 和 worker support 五方完全一致；所有 enabled 且
进入当前 profile 的模型均通过目标机健康检查。

### 阶段 F：发布闭环

- 更新 release inventory、release check、RPM 和全部文档；
- 执行 source 与 installed-tree 双重验证；
- 重跑全套测试和 release readiness；
- 只有真实 Phase 12 数据满足时才解除对应发布门，manifest 工程完成本身不解除准确率/桌面验收门。

退出条件：删除或篡改任意 bundle 成员都会阻止发布，RPM 不携带权重/凭证，用户数据升级与卸载合同保持。

## 13. 验收标准

本修复只有同时满足以下条件才算完成：

- [ ] bundle 恰好覆盖注册表 20 个模型；
- [ ] 每个 manifest 与 revision lock、license inventory、worker lock 和 registry 完全一致；
- [ ] 所有文件的 size/SHA 来自固定 commit 的实际下载字节；
- [ ] 文件选择经过人工策展且保持 worker 需要的原始布局；
- [ ] remote code 被正确分类、哈希、展示并在无网络 sandbox 中运行；
- [ ] 普通 API 和 WebUI 不接受用户替换发布 manifest；
- [ ] 安装仍要求用户明确预检和一次性二次确认；
- [ ] pyannote 条款和 token 处理满足 gated 要求；
- [ ] 14 个当前使用模型均有真实目标机健康证据；
- [ ] 未实现/disabled 模型不会显示为普通可安装；
- [ ] source tree、RPM installed tree 和 release inventory 的 bundle 字节一致；
- [ ] 离线 verifier、单元、集成、前端、RPM 和发布测试全部通过；
- [ ] 文档不再声称缺失或未验收的工件已经完成。

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 上游固定 commit 删除或 gated 访问撤销 | 无法重新生成或安装 | 保留冻结 manifest 和许可证证据；发布前验证可访问性；不得绕过授权 |
| 仓库含多种权重格式 | 下载/磁盘膨胀或加载错误 | 人工 selection，权重 index 闭包检查，拒绝默认全仓下载 |
| Git/LFS/Xet 哈希语义混淆 | manifest 哈希错误 | 统一下载实际字节后流式 SHA-256 |
| remote-code 布局被重写 | Transformers 离线加载失败 | 保留上游相对布局，以 kind 和 sandbox 隔离 |
| pyannote 引用其他 repository | 单仓库 downloader 无法表达完整来源 | 审计传递依赖；必要时先设计多来源 manifest，不手工拼 cache |
| manifest 存在但 worker 未实现 | 用户下载大量文件后健康失败 | 独立 `worker_implemented/installable/enabled` 状态，安装前阻止 |
| 生成器泄漏 token | 凭证暴露 | token 只进 header；日志脱敏；fixture 测试异常路径 |
| release check 只检查目录存在 | 缺件仍误通过 | 从索引展开并验证每个 manifest SHA 与交叉合同 |
| 上游相同 commit 返回不同字节 | 供应链异常 | 空缓存双重生成；字节不一致即阻止发布 |

## 15. 建议首个实现批次

第一批提交应只包含可独立审核的基础设施，不下载全部大模型：

1. remote-code 路径合同修复及测试；
2. bundle schema、loader 和离线 verifier；
3. 独立 manifest tool 骨架和 lock；
4. selection schema；
5. Whisper Tiny 的真实 manifest 与端到端测试；
6. release check 和 RPM 对单模型 bundle 的验证。

该批通过后，再按第 11.5 节顺序扩大到生产模型。这样能先证明供应链设计正确，再承担多模型的大量
下载、gated 授权和 GPU 健康验收成本。
