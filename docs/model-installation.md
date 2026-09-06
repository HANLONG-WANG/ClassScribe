# 模型安装、供应链审计与离线运行

## 权限边界

模型下载不是 core 任务恢复的一部分，也不是“缺文件时自动修复”。调用方必须先以用户
点击／明确命令调用 `request_user_install()`，向用户展示预计下载 bytes、最终占用 bytes、
所需临时空间、当前可用空间和依赖环境；它返回 10 分钟有效、一次性消费的随机确认
token。没有 token、过期 token 或复用 token 均返回
`MODEL_INSTALL_NOT_USER_INITIATED`，且 downloader 不会被调用。

下载凭证只能来自系统 keyring 或 `RestrictedCredentialEnvironment` 的独立 0600 文件。
manifest、普通 YAML、日志、诊断和供应链记录均不得写入 token。

## Manifest v1

机器 schema 位于 `protocol/schema/v1/model-manifest.schema.json`。每个 manifest 必须包含：

- 安全的稳定 `model_id`、worker ID 和上游 repository；
- 完整、40 位小写十六进制 commit SHA revision，禁止 `main`、tag 或短 SHA；
- 下载量、安装量及字符串型依赖环境；
- 冻结的 SPDX／`LicenseRef-*` 许可证 ID、官方 HTTPS 许可证 URL，以及是否必须接受上游条款；
- 安装后的每个文件相对路径、精确 byte size、SHA-256 和类型；
- `trust_remote_code` 标志。

`ManifestFile.path` 同时是上游 repository 路径和安装 revision 内路径，下载与安装必须原样
保留布局，不重写、搬移或复制。若 `trust_remote_code=true`，至少一个文件必须显式分类为
`remote_code`；该文件可以位于任意安全的根级或嵌套上游路径。反之 manifest 不得偷偷携带
remote-code 文件。remote code 的信任边界由显式 kind、逐文件 SHA-256、bundle 记录的
manifest SHA-256、`supply-chain.json` 审计和无网络 Bubblewrap 隔离共同建立。绝对路径、
`..`、重复路径、symlink、未列出的文件和缺失文件全部拒绝。

生产清单位于只读的 `config/model-manifests/v1/`：`bundle.v1.json` 索引注册表全部 20 个模型，
并以 SHA-256 绑定同目录的 20 个单模型 manifest。core 只从该索引加载清单并核对 registry、
revision lock、许可证 inventory、worker lock 和实际 lock bytes；release checker 另行核对人工
selection 与 release inventory。缺件、额外成员、任一 byte 漂移或合同不一致都会使整个 bundle
失败。普通 API 不接受客户端 manifest；公开的只读 manifest 接口仅用于展示发布方已经冻结的
内容与 SHA。

bundle 生成器属于单独冻结依赖的发布期工具，只能由明确的发布操作联网访问固定 Hugging Face
repository/commit/文件集合；gated repository 还必须显式传入已接受条款的 repository ID。生成器
凭证只进入请求 header，双 clean generation 和独立离线 verifier 通过后才允许覆盖 production JSON。
该工具及其联网权限不进入 core 的运行期解析、推理或任务恢复路径。用户确认的模型安装是另一条、
逐模型的一次性联网授权，不能复用发布期生成授权，也不能由 runtime 自动触发。

## 安装事务

1. 在模型根同一文件系统的 `.staging` 建 0700 临时目录。
2. downloader 只接收 manifest repository、固定 revision、目标目录和文件清单，并返回
   实际 resolved revision；不一致立即失败。
3. 逐文件流式复算 size／SHA-256，拒绝额外文件和 symlink，并计算稳定 aggregate hash。
4. 在 payload 写入 0600 `supply-chain.json`，包含原 manifest、来源、下载量、安装时间、
   aggregate hash 及全部代码／模型文件哈希。
5. 以同文件系统 `os.replace` 原子发布到
   `${XDG_CACHE_HOME}/classscribe/models/<model>/revisions/<commit>`。
6. 在强制离线环境和同一 Bubblewrap 挂载策略中，让对应 worker 对一个非空、单声道 16 kHz、
   16-bit PCM、最长 15 秒的真实短音频完成 load → 对应任务 infer → unload；回调必须报告实际
   结果、环境和实测 VRAM，不能只看文件存在。
7. 健康后记录 `model_installations`，再原子写 `active.json`。任一环节失败会清理新 revision
   并保持／恢复旧 active revision。

HTTP 流程固定为两步：`POST /api/v1/models/{model_id}/install` 接收空 JSON 对象，按 model ID 从
内置 bundle 取 manifest，校验冻结许可证披露、空间和环境并返回确认 token；
`POST /api/v1/models/{model_id}/install/confirm` 必须再次提交 token、
健康检查录音 UUID、语言、可选参考文本和 `terms_accepted`。需要 gated 条款却未明确接受、token
对应其他模型，或 manifest 许可证与 `config/model-licenses.v1.json` 不一致时，在联网前拒绝。

生产 downloader 只允许 `owner/repository`、完整 commit 和 manifest 中逐项列出的文件；只访问
Hugging Face 官方 HTTPS host，拒绝凭证 URL、跨 host/非 HTTPS redirect、已存在目标、symlink、
超出声明大小及短写。token 仅进入 Authorization header，不进入 source URL 或供应链审计。

每个 worker 环境按自己的 `uv.lock` SHA 建在用户 cache。安装确认阶段复制 worker 与共享协议，
使用 `python -m venv --copies` 和 `uv sync --frozen --no-dev --no-editable` 创建不可变环境；完成标记
绑定 worker ID 和 lock SHA。运行器只使用该环境中的解释器，不回退系统 site-packages，也不在
任务期间解析或下载依赖。

安装健康通过并切换 active revision 后，core 请求 resident supervisor 刷新。刷新只调用
`WorkerEnvironmentProvisioner.resolve()` 读取已经完成且 lock SHA 匹配的环境；它不会调用
`ensure()`、`uv sync` 或 downloader。课堂一次性 worker 和 IBus 常驻 worker 都遵守这一分界：
环境创建只属于用户确认的安装事务，普通推理缺环境时立即报告未完整安装。

manifest diff 会列出 added、removed、hash/size/kind changed 和其中所有 remote-code
changed 文件，UI 必须在升级确认前展示。旧 revision 保留用于回滚；回滚通过原子 active
指针切换并重新验证完整性。活动 revision 不能删除，必须先切换；删除只接受经过 identifier
和完整 commit 校验的非活动 revision。

## 运行期离线失败

`resolve_for_runtime()` 只有本地读取与哈希验证路径，没有 downloader 参数或网络客户端。
每次使用都会验证 active 指针、审计记录、manifest、全部文件与 aggregate hash。缺少 active、
文件不全、内容被改、control path 是 symlink 或审计损坏时返回
`MODEL_NOT_FULLY_INSTALLED`；任务失败但绝不访问上游补文件。

worker 环境无条件覆盖：

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
```

systemd unit 和 RPM 启动脚本也重复设置这些变量，形成库层与进程层双重约束。后续 worker
RPC 调度必须使用 `WorkerSandbox.command()`：Bubblewrap 取消全部 namespace（包括网络）、
清空环境，只读挂载单个模型 revision、单个获批音频与 worker 环境，只给精确输出目录和
Unix socket 目录写权限，并仅按 GPU 租约显式挂载 `/dev/nvidia*`。它不挂载用户 home 或
完整 ClassScribe 数据根，因此 remote code 不能借 Python API 绕过路径参数读取其他数据。
