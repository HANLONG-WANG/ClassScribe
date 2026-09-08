# FireRed GPU 切换后的 worker 环境失效：根因与修复方案

日期：2026-09-08。调查基线：`0730d4b`。方案已落地；实现与验证结果见文末。

## 根因与证据

课堂转录报错发生在 `SandboxedModelInvoker._load()` 调用
`WorkerEnvironmentProvisioner.resolve("firered")` 时，早于 sandbox 启动、模型加载和 CUDA 推理。
环境路径由 `worker_id / lock_sha256 / source_sha256` 决定；源码摘要包括 worker、共享协议源码和构建配置。
因此错误中的 “pinned lock” 并不能说明依赖锁发生变化。

本机 `~/.cache/classscribe/worker-environments/firered` 的只读检查结果：

| 项目 | 摘要/结果 |
| --- | --- |
| 当前及两个缓存环境的 uv.lock SHA-256 | `14543cb7df6705b7ec82ef75e4a265bf2edf9e7f8b4e9465560ac4114b26f445` |
| 当前源码 SHA-256 | `b92068edd505238c5696416832ff03170715156ca6ac7f0723c3d2acc8bfe08f` |
| 最近缓存源码 SHA-256 | `1a6574125d29a80d136aa9a8b272a43cf490557528ade77ba54d912b6325e7ce` |
| 更早缓存源码 SHA-256 | `9d1ba2f9fab33dad8e9a0e5659802aeb449e29d78c4a4743d6c1ac5d56ab8bda` |
| 缓存与当前源码的差异文件 | 仅 `workers/firered/adapter.py` |

两个缓存 adapter 分别与 `HEAD^` 和 `HEAD^^` 完全一致。`0730d4b` 新增
`_auxiliary_use_gpu()`，把 VAD/LID 的 `use_gpu=False` 改为按请求设备选择，导致源码指纹变化。
当前指纹对应的环境尚未创建；直接对实际缓存调用只读 `resolve()` 已复现同一异常。

完整因果链：GPU 支持修改 adapter → 源码指纹变化 → 旧环境不可复用 → 推理路径只调用
`resolve()`、不执行环境准备 → 报错。单纯切换设备参数不会改变环境键；此次触发因素是配套代码更新。
该校验也影响共享 firered worker 的其他模型及输入法冷启动，不只影响 GPU 路径。

产品层缺口：

- `InstalledModelHealthChecker.__call__()` 在安装健康检查中调用 `ensure()`，但源码升级后没有独立环境修复入口。
- `ModelManager.installation_stage()` 只依据旧安装审计的健康结果；课堂 `preflight()` 只检查模型安装记录。
- `ClassScribeService.verify_model()` 校验模型文件后即标记健康，未验证当前 worker 环境。
- 健康模型不能直接走原安装流程重装同版本：`_reusable_download()` 只接受尚未健康的记录，否则会报 “revision is already installed”。

## 修复方案

### 1. 当前环境恢复（优先）

1. 在维护阶段暂停新的相关加载，释放课堂和输入法中使用 firered 的常驻 worker。
2. 使用与服务一致的 `resource_root()` 和 `AppPaths.cache`，对 firered 调用现有 `ensure()`。
   它会在新指纹目录内复制当前源码，通过 frozen lock 准备虚拟环境，完成校验后原子发布。
   复用 uv 下载缓存；依赖缓存不足时可能需要联网。此步骤无需删除或重新下载模型权重。
3. 对新环境执行 `resolve()`，确认返回当前源码指纹路径。
4. 使用已安装模型和合格短音频分别验证 VAD、LID 的显式 `cuda:0` 推理，以及输入法的 CPU 路径。
   验证成功后恢复任务，并清除受影响阶段的失败状态或按现有重试机制重跑。

保留旧环境便于排查。不要手改 `complete.json`、复制旧目录冒充新指纹，或取消源码校验。
也不要把推理中的 `resolve()` 直接替换成可能联网的 `ensure()`。

### 2. 增加明确的环境状态与修复入口

- 在 `models/environment.py` 增加只读环境检查结果，区分 ready、missing、source_changed、lock_changed、incomplete，
  返回当前/缓存指纹与 worker ID；保留 `resolve()` 的严格、离线行为。
- 提供独立“修复运行环境”操作，复用 `ensure()` 和已安装权重。按 worker 加互斥，避免同属 firered 的模型并发创建相同环境；
  修复期间与模型加载协调，完成后失效旧常驻状态。
- 将模型文件完整性、worker 环境就绪、设备健康结果分别展示。环境失效时提示“运行环境需更新”，不继续显示无条件可用。
- 更新“校验模型”：至少检查 worker 指纹；若该操作只校验文件，明确标注“文件校验通过”，不可据此宣称能推理。
- 在后台任务的模型加载前做环境预检，给出可操作错误及修复入口；保持请求创建阶段轻量，避免重新引入全量权重校验。
- 健康审计记录 worker ID、lock/source 指纹、请求与实际设备。代码升级后旧健康结果不得作为新环境已验证的证据。
- 源码不匹配错误应说明“依赖锁一致，但 worker 源码已更新”，避免引导用户错误地重装 CUDA 或模型。

### 3. 验证与验收

- 保持锁文件不变，仅修改 adapter：检查返回 source_changed；推理拒绝旧环境；修复后解析到新路径且保留旧目录。
- 修改共享协议或锁文件、缺失完成标记、损坏缓存源码时，分别覆盖检测和修复行为。
- 健康权重 + 过期环境：界面不能声称完全就绪；独立修复不下载权重；单纯文件校验不清除环境错误。
- 两个模型同时修复同一 worker：仅一次构建；失败不留下可被运行时使用的半成品。
- 真实 GPU：课堂 VAD → LID → 后续模型串行使用 GPU，切换卸载；显式 CUDA 不可用时清楚报错；输入法仍使用 CPU。

本次已运行 `tests/unit/test_worker_environment.py`，9 项通过，其中已有源码变更拒绝旧环境并重建的测试。
已只读复现本机实际异常；尚未重建环境或执行真实 GPU 推理。现有证据确定了此次阻塞原因，恢复后的 CUDA 状态仍需上述验收。


## 实现与验证结果

- `WorkerEnvironmentProvisioner.inspect()` 提供只读环境状态；运行时错误包含失效原因与修复指引。
- `ensure(..., repair=True)` 支持独立修复，使用跨线程/进程文件锁串行构建，损坏目录移至隔离目录后重建。
- 新增 `POST /api/v1/models/{model_id}/environment/repair`，保留已安装权重；课堂任务运行时拒绝修复，修复前释放输入法常驻模型，完成后刷新状态。
- 模型列表返回独立 `worker_environment` 状态；模型页提供修复按钮、进度和结果，模型选择器不再将过期环境视为就绪。
- 校验模型同时检查当前环境；安装健康审计新增 worker 锁、源码指纹及请求设备。环境修复本身不声称已经完成真实推理。
- 本机 firered 环境已完全使用离线缓存重建，当前指纹 `b92068ed…` 状态为 `ready`，保留旧环境及权重。
- 在 RTX 4070 上通过生产 WorkerSandbox，使用既有中文测试短音频，分别完成 firered_vad / firered_lid 的 `cuda:0` 与 `cpu` 推理，四项均健康。
- 新增源码失效、损坏目录修复、并发仅构建一次、文件校验不掩盖过期环境、修复不重下载权重和前端状态恢复测试。

尚未跑完整课堂长音频端到端任务；本次真实验证覆盖此次出错的 FireRed 环境与模型推理边界。

最终检查：后端相关测试 48 项通过，前端相关测试 11 项通过；Python/TypeScript 类型检查、Ruff、ESLint、Prettier 和前端生产构建通过。
