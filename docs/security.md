# 本地安全与隐私

## API

- core 的配置只允许 `127.0.0.1`、`::1` 或 `localhost`，运行时中间件再次拒绝非
  loopback 客户端，并拒绝非 loopback 的 HTTP `Host`，防止通过 DNS rebinding 绕过来源边界。
- 首次启动以 CSPRNG 生成 32 bytes（256-bit）url-safe bearer token，保存在
  `${XDG_CONFIG_HOME}/classscribe/api-token`。目录为 `0700`，文件必须是当前用户拥有的
  普通文件、非 symlink、权限严格 `0600`。
- `/api/` 下所有读写路由都要求 `Authorization: Bearer ...`，使用常量时间比较；不使用
  Cookie，因此不建立可被跨站请求自动携带的认证状态。
- WebUI 首页只在运行时响应中注入 bearer token；token 不进入静态构建产物、URL 或日志，
  浏览器读取后立即从 DOM 移除。
- POST/PUT/PATCH/DELETE 还必须提交 `X-ClassScribe-CSRF-Token`，其值与 bearer token 常量时间
  比较；缺失或错误均在进入业务层前拒绝。
- 所有响应添加 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff` 和
  `Referrer-Policy: no-referrer`。

## 上传与 worker 路径

浏览器文件名仅作为数据库显示元数据，物理 job 和上传文件均使用规范 UUID。预解码
检查限制 8 GiB、90 分钟、允许的音视频后缀、最多 8 声道和 192 kHz；FFmpeg 被固定为
无 stdin、2 线程、5400 秒硬上限、丢弃视频并输出 16 kHz mono S16。阶段 4 会在探测和
解码层继续实施容器／codec 检查。

录音媒体和导出下载只接受 UUID，并从数据库保存的受控路径反查；响应只暴露资源 URL、显示名、
大小和 SHA-256，不暴露 XDG 物理路径。词典材料同样拒绝带目录的文件名和非法材料枚举。

worker 只可接收 `jobs/`、`resampled/` 或 `tmp/` 下已经存在的规范普通文件。系统绝对
路径、`..`、symlink、非 UUID 文件 ID 和根外路径均拒绝。systemd user units 已增加
no-new-privileges、只读系统目录和地址族限制；core 必须保留出站 HTTPS 能力，供已认证、
二次确认的模型安装事务使用，因此不能设置会同时封死该唯一联网入口的 `IPAddressDeny=any`。
core 仍只监听 loopback；模型 worker 则由 Bubblewrap 取消网络 namespace，并只获得单模型、
单音频和精确 output/socket 挂载。

## 实时听写边界

- engine、hotkey 与 dictationd 的 Unix socket 位于当前用户的 `XDG_RUNTIME_DIR/classscribe`；
  目录为 `0700`、socket 为 `0600`。连接前复验普通 socket、非 symlink、owner 与当前 UID；
  请求/响应均有大小和超时上限。
- 麦克风只经 PipeWire/GStreamer 转成内存中的 16 kHz mono S16LE 小帧。默认不创建 WAV、数据库
  听写历史或正文日志；取消、commit、异常和自动分块都会释放不再需要的 PCM。
- FireRedLID 的 `lid_pcm` 只允许最多 30 秒 PCM。worker 如需适配 file-only 官方 API，只在其私有
  临时目录生成 WAV，并在返回/异常时删除；路径从不返回调用者。
- Portal 状态只含可用性、脱敏诊断和时间，原子写为 `0600`。core 读取时拒绝 symlink、非普通文件、
  异 UID 和超过 16 KiB 的载荷；已认证 API 才能读取汇合状态。
- GPU 租约 IPC 采用同 UID、本地、有界协议；dictation priority 0 阻止新课堂 GPU 片段，只允许
  已运行课堂片段到安全边界，不强杀或泄露另一个 worker 的状态。
- resident handoff 由 core 原子写入 `0600` 清单；dictationd 拒绝 symlink、异 UID、宽权限、超限
  清单，以及不在相邻 `workers/` 目录的 socket。清单只含 model ID/revision、socket 和状态，不含
  模型 token、音频或正文。常驻 VAD/LID 不暴露 GPU，ASR 只挂载明确枚举的 NVIDIA device。

## 下载凭证与网络

正式推理不包含网络客户端。模型管理器需要凭证时，只可使用系统 keyring 或
`RestrictedCredentialEnvironment` 生成的独立 `0600` 环境文件；普通 YAML、仓库、
日志和诊断包不得包含凭证。该文件拒绝非法变量名、空值、换行和 symlink。模型下载
只能消费一次性用户确认，完整事务及离线变量见 [模型安装文档](model-installation.md)。
core 进程具备出站地址族不等于默认联网：唯一网络实现是安装确认时注入的 downloader；
`resolve_for_runtime()`、课堂流水线、IBus 和 worker 均没有 downloader 引用。发布验收仍需在
阻断外网环境完成所有已安装模型的推理，以证明不会发生隐式请求。

发布期 manifest 生成器是独立冻结的工具环境，不由已安装的 core、systemd unit 或 worker 调用。
只有明确的发布操作可让它联网读取人工 selection 中列出的固定 repository/commit；gated 来源还需
显式声明已接受条款。两次空 cache 生成必须字节一致并经另一离线 verifier 复核后，才可替换 20 个
manifest 与 bundle 索引。发布期授权不授予 runtime 联网，也不替代普通用户对每次模型安装的二次
确认；token 只进入 HTTPS Authorization header，不写入 JSON、日志、异常、RPM 或诊断包。

发布阻塞 waiver 使用独立规范化 JSON，并由 release manifest 以固定路径和 SHA-256 绑定到 release
version、facts cutoff 与 registry revision。checker 同时保留原始 `checks`/`reasons`，只在 waiver
精确覆盖授权的三个 gate、acknowledgement 完整且 source/installed bytes 一致时生成
`effective_checks` 和 `ready_with_waivers`。缺失、symlink、字节篡改、版本漂移、额外 gate 或宽泛 scope
都会恢复 `blocked`。该机制只改变自动阻塞决策，不产生许可证权利或质量、性能、桌面兼容性证据。

## 日志和诊断

结构化日志固定包含 timestamp、level、service、request/job/segment/model、stage、
duration、VRAM 和 error code。正文、各文本层、Authorization、HF token、密码和 secret
键递归替换为 `[REDACTED]`。诊断 API／zip 只在本机产生，汇总 Fedora、内核、桌面／
会话、GPU/驱动/显存、FFmpeg、PipeWire、IBus、portal、worker、模型、GPU 租约／队列、
最近错误和本地指标；没有上传或遥测入口。

## 删除级别

- `derived_only`：清理派生音频、冗余候选文件和相关 cache，明确保留导出和数据库决策。
- `job`：用户明确删除整个 job、其数据库关系、来源和导出。
- `all_local_data`：服务停止后，以精确确认短语清空 config/data/cache/state/runtime 的
  应用根内容；每个根都重新验证 owner、权限和范围，绝不递归到 XDG base 或 home。
