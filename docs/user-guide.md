# ClassScribe 用户手册

## 1. 安装与首次启动

ClassScribe 面向 Fedora 普通用户。安装 RPM 后不需要也不应以 root 运行服务：

```bash
systemctl --user enable --now classscribe-core.service
classscribe-doctor
curl --fail http://127.0.0.1:8765/healthz
```

桌面菜单中的 **ClassScribe** 会启动用户级 core 服务，健康后打开
`http://127.0.0.1:8765/`。API 只监听 loopback；bearer token 和 CSRF token 位于用户的
0700 runtime/config 目录。WebUI 首页会在运行时为当前页面注入 token，浏览器不会把它写入
URL；不要把 token 复制到截图、日志或工单。

若 `classscribe-release-check` 返回非零，先阅读输出中的 `reasons`。它表示当前树不能签名
发布，不表示已有本地数据损坏。

## 2. 安装模型

RPM 不携带任何模型权重。进入“模型管理”，按模型名称／model ID 从 RPM 自带的只读 manifest
bundle 选择；不需要、也不能选择或上传 manifest 文件。界面会显示发布方固定的完整 commit、
manifest SHA、下载量、安装后占用、临时空间、独立 worker 环境、许可证和 gated 条件。只有再次
明确确认才可联网下载；确认 token 十分钟失效且只能使用一次。

安装事务先写同文件系统 staging，逐文件核对大小和 SHA-256，运行离线真实短音频健康检查，
最后原子切换 active revision。失败会删除未完成 staging 并保留旧 active。运行时缺文件只会
报 `MODEL_NOT_FULLY_INSTALLED`，绝不会暗中联网补文件。

受 gated 条款保护的模型（例如 pyannote Community-1）必须由用户在上游接受条件并提供受限
凭证。凭证只能进入系统 keyring 或 0600 专用文件，不得写入 manifest。

## 3. 课堂转录

默认使用本地模型。也可选择 **Azure MAI-Transcribe-2** 在线转写，无需安装本地语音模型：

1. 在用户 `config.yaml` 的 `privacy` 分组中设置 `runtime_offline: false`，或为 core
   进程设置 `CLASSSCRIBE__PRIVACY__RUNTIME_OFFLINE=false`，然后重启。
2. 在“设置与诊断 → Azure MAI 在线转写”填写 Azure Speech HTTPS Endpoint 和 Key。
   Key 仅保存在当前后端进程内，重启后清除；也支持进程环境变量
   `AZURE_SPEECH_ENDPOINT` 和 `AZURE_SPEECH_KEY`。清除内存凭据不会删除环境变量配置。
3. 导入页选择 MAI，勾选上传授权后提交。上传的是实际转写范围的无损 FLAC 及所选词典术语，
   不上传原文件名。Azure 可能计费；取消本地请求不保证云端停止处理或计费。

MAI 使用 Speech REST `2025-10-15`，请求 verbatim 文本和 word 时间戳；自动／混合语言不强制
指定 locale。模型版本未被服务返回时显示 `service-unreported`，不伪造模型 revision。
接口依据：[Microsoft MAI 文档](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-transcribe)。

在线请求不会自动重发。发送后断网、取消或进程退出导致结果未知时，需要确认可能重复计费后
创建新任务；已经保存响应的任务重试只重新解析响应。原始响应和不含密钥的请求元数据保存在
该任务的本地目录，删除任务时一并清理。当前仍保留单文件 90 分钟上限，上传必须小于 250 MB。

勾选“导入后试听并选择转写范围”后，可拖动波形选区或输入起止秒数，试听、重设，
再选择整段或剪辑转写。剪辑生成独立录音，原文件保持不变。转写稿、回听和字幕时间从片段
起点计算；界面显示原录音位置，JSON 的 `source` 字段保存原录音 ID 和偏移。
例如原录音 10:00 开始的片段中第 5 秒，对应原录音 10:05，偏移只叠加一次。

点击转写句段会立即播放并在句尾停止；再次点击可重播，另有“仅定位所选句段”。
缺少词时间戳时保留完整句段文本；完全没有有效时间戳的文本仍可编辑和导出全文，
但不能精确回听，也不会写入 SRT/VTT。在线句段不支持调用本地模型重跑，重新上传需创建新任务。

本地模型的操作如下：

1. 在“上传”选择音频或视频；不要把路径粘入 API，上传后只使用 UUID。
2. 选择中文、日语、英语或自动／混合语言。
3. 选择自动最佳、手动主模型或严格单模型。手动主模型仍允许质量 fallback；严格单模型不允许。
4. 可选词典、说话人数提示和 TXT/MD/JSON/SRT/VTT/CSV 输出。
5. 创建任务后可关闭浏览器。checkpoint 持久化，服务重启后从第一个未完成点恢复。

课堂流水线固定为：MOSS 结构与说话人粗时间轴 → 语言专用短句 ASR → 自动质量门与按需复核 →
时间对齐共识 → 确定性术语 → 严格不改字标点 → 门控精细对齐 → 自动导出。MOSS 文本不是无条件
最终正文；模型也不能以“补全语义”为理由增加未被音频或明确术语规则支持的 token。

任务页可暂停、继续、取消和按句段重试。IBus 请求 GPU 时，课堂任务只在 checkpoint 安全边界
暂停；听写结束后只恢复由这次抢占暂停的任务。

## 4. IBus 语音输入

在系统输入源中添加 **ClassScribe Voice**，并启用：

```bash
systemctl --user enable --now classscribe-dictationd.service
systemctl --user enable --now classscribe-hotkey.service
ibus restart
```

输入法属性可选择语言、fast/balanced/accuracy、自动最佳或已预加载的具体模型，以及按住或切换
说话。按下后先进入 ARMING，再显示 preedit；松开后才 commit。Esc、麦克风失败、worker 崩溃或
GPU 超时必须清空 preedit 且不提交。

Wayland 全局快捷键通过 XDG Desktop Portal GlobalShortcuts 注册，桌面可能显示授权对话框。
Portal 不可用时，切换到 ClassScribe 输入源后使用输入法内的麦克风属性，这是稳定兜底。长听写
按自然停顿和 20～30 秒硬上限分块，以 absolute sample + token time 去重，不做固定字符裁剪。

## 5. 词典与课程材料

可以手工录入 canonical、读音、alias、语言与权重，也可导入 TXT、Markdown、CSV、PDF、PPTX、
讲义或教材。自动抽取项默认是低权重建议，只有用户确认后才成为高权重规则。术语上下文受每个
模型的 token 预算限制；确定性纠正必须命中已登记 alias，并记录 rule ID 和前后差异。

词典不会把生成式改写写入忠实层。中文繁体仅是最终显示／导出变体，不回写忠实简体文本。

## 6. 校对

转录页同步显示波形、说话人、语言轨和文字。默认层是自动最终稿，可切换 raw、faithful、smart、
user。低置信筛选只影响显示，不隐藏时间轴证据。点击句段可查看：

- 原始候选、model ID、完整 revision、decode 参数和原始模型置信度；
- QA issue、复核路由、共识来源和每个最终 token 的 provenance；
- 术语、标点、对齐及 fallback decision event；
- 说话人匿名 ID 与任务内显示名。

用户编辑只写 user 层并带 optimistic version；撤销／重做保留审计。重命名说话人不会修改模型
生成的 speaker evidence。

## 7. 导出

任务不等待人工校对即可自动导出。也可从“导出”重新生成 TXT、Markdown、JSON、SRT、VTT 或
CSV，选择 faithful/smart/user 和逐句／段落。user 为空时按 user → smart → faithful 明确回退。
SRT/VTT 只采用最终句／词时间，最多两行，保护专名、数字与单位。若精细对齐失败，导出会明确
标记 coarse timing，不伪造精度。

## 8. 诊断与排错

先运行 `classscribe-doctor`，再查看“设置与诊断”。重点核对 Fedora/内核/桌面会话、FFmpeg、
PipeWire、IBus、Portal、GPU 驱动、worker health、model revision、GPU lease、最近结构化错误、
RTF/VRAM/OOM 与 IBus p50/p95。

诊断包只有脱敏后的 `diagnostics.json`，home 路径替换成 `[HOME]`，不含音频、转录正文、preedit、
commit 文本、token 或凭证，0600 原子写入且从不自动上传。详细 error code 和恢复步骤见
`troubleshooting.md`。

## 9. 删除数据

ClassScribe 提供三个明确层级：

- `derived_only`：删除中间派生文件和缓存，保留数据库决策与最终导出；
- `job`：删除一个任务及不再被其他任务引用的录音；
- `all_local_data`：输入精确确认短语 `DELETE ALL CLASSSCRIBE DATA` 后清空全部 ClassScribe XDG 根。

在“历史转录稿”中，对已结束的任务可分别选择“清理派生数据”或“删除任务及录音”。
在“设置与诊断”中输入上述完整短语后，可执行全部本地数据清除；执行后请重启应用。
活动任务必须先结束或取消，API 会拒绝删除仍在运行或可恢复的任务。

RPM 升级或卸载从不拥有用户的 XDG 目录，因此不会自动删除录音、模型、设置或导出。卸载后如需
清空数据，应先备份所需导出，再通过受控删除功能执行，而不是用 root 递归删除 home。

## 10. 离线隐私边界

正式推理设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`HF_DATASETS_OFFLINE=1`；worker
通过无网络 Bubblewrap 沙箱读取单个模型 revision 和单个获批音频。core 无遥测，日志不记录正文，
IBus PCM 默认只在内存中存在。唯一允许联网的路径是用户主动确认的模型安装／升级。公开 benchmark
结果不替代用户私有课堂 gold，私有 gold 不进入 RPM、诊断包或远程服务。

## 派生文件与运行环境清理

设置中的派生文件保留期接受 1 到 3650 天。保存后后台按此期限清理已完成或已取消
任务的临时派生文件，保留原始录音、数据库证据和导出；活动任务及可重试的失败任务
不参与自动清理。原始听写音频保存目前不支持，控件明确禁用，听写 PCM 默认只在内存中处理。

模型页的“清理旧运行环境”只处理运行环境缓存，不删除模型权重。正在使用的环境会
被跳过；旧版本与损坏备份按保留策略回收。显示的逻辑体积不等同于实际可释放磁盘空间，
旧缓存资料不完整时会明确说明。

更新本次 worker/protocol 代码后，已有冻结环境可能提示来源变化。实际推理前使用
已有“修复运行环境”操作重建，然后执行对应模型健康检查。旧基准排名需按新评分合同
重新生成。缺乏可靠词时间的硬切上下文会保留全文并提示复核，不会按字符比例删除文字。
