# Fedora 安装与普通用户服务

## 只读依赖检查

`classscribe-doctor` 只执行文件、命令版本、GObject introspection namespace 和
GStreamer element 探测；它不会运行 `dnf`、`rpm-ostree`、`sudo` 或更改系统配置。JSON
报告覆盖 NVIDIA kernel driver／`nvidia-smi`、FFmpeg／FFprobe、PipeWire、WirePlumber、
GStreamer 与常用音频 element、IBus、PyGObject、GLib／GTK 4／IBus introspection、
gcc／g++／make／CMake／pkg-config／Git、uv、Bubblewrap、Node.js 和 pnpm。NVIDIA 项是可选项：缺少
GPU 不阻止 CPU 模式；也不检查或要求完整 CUDA Toolkit。

```bash
classscribe-doctor
```

同一命令同时输出只读依赖矩阵、脱敏系统/GPU/component 诊断和配置摘要；`--bundle PATH` 另写
`0600` 的 `diagnostics.json` zip，但不会上传。退出码仍由必需依赖矩阵决定，因此诊断内容可生成
而命令仍以 1 如实报告缺少 PyGObject、Active LTS Node 或其他必需项。

退出码 0 表示全部必需项可用，1 表示报告中至少一项必需依赖缺失或不兼容。2026-09-03
依据 [Node.js 官方发布计划](https://github.com/nodejs/Release/blob/main/schedule.json) 复核：
24 是 Active LTS，22 是 Maintenance LTS，26 仍是 Current。检测器要求推荐的 Active LTS
24，并在源码中记录复核日期；每次发布必须重查官方表。

每个 `workers/<id>` 都有独立 `.venv`、`pyproject.toml` 和 `uv.lock`，并在自己的 Python
进程中运行：

```bash
uv run --offline --frozen --project workers/<id> python workers/<id>/worker.py --cuda-check
```

输出同时报告 PyTorch wheel 的构建 CUDA 版本、驱动可用性与 device count，并明确
`cuda_toolkit_required=false`。某个 worker 的成功不能替代其他 worker 的独立检查。

## RPM 产物

`packaging/rpm/classscribe.spec` 是可构建的 noarch 基础包。它安装：

- `/usr/libexec/ibus-engine-classscribe`；
- `/usr/share/ibus/component/classscribe.xml`；
- `/usr/lib/systemd/user/classscribe-{core,dictationd,hotkey}.service`；
- `/usr/share/applications/classscribe.desktop`；
- `/usr/share/icons/hicolor/scalable/apps/classscribe.svg`；
- core、共享协议和三个薄桌面进程的 Python 源及受控启动脚本。

RPM 文件清单不得出现用户 home、XDG config/data/cache/state/runtime 或模型 revision。
安装、升级与卸载都不拥有这些目录，因此不能覆盖或删除录音、导出、设置、凭证和模型。
模型只能通过用户主动确认的模型管理器进入用户 cache。

## systemd user 服务与 SELinux

三个 unit 都不含 `User=`，因此由调用者的 user manager 以普通用户身份运行；均使用
`UMask=0077`、`Restart=on-failure`、重启间隔与 60 秒 burst 限速。它们设置
`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 和 `HF_DATASETS_OFFLINE=1`。core 允许
AF_UNIX/AF_INET/AF_INET6，但 HTTP 只绑定 loopback；保留出站 HTTPS 是为了让已认证、二次确认
的模型安装入口可用。正常推理没有网络客户端，模型 worker 另由 Bubblewrap 取消网络 namespace。
其他两个薄进程只允许 AF_UNIX；全部启用 systemd 的 no-new-privileges 和只读系统目录加固。

core 启动时同时启动 GPU lease server 与 `DictationWorkerSupervisor`。supervisor 不 provision 环境，
只解析已安装 revision 和已完成的 frozen worker 环境；在 VRAM 总预算内常驻 profile 所需 ASR，
并以 CPU 常驻 VAD/LID，再把 `0600` 路由清单交给 dictationd。模型安装确认或 profile 应用/回滚会
请求刷新；core 关闭时先发布不可用状态并卸载/终止所有受管 worker。dictationd unit 不再需要也
不得自行生成任意 worker socket。

只默认启动 core：

```bash
systemctl --user enable --now classscribe-core.service
systemctl --user status classscribe-core.service
curl --fail http://127.0.0.1:8765/healthz
```

unit 和 spec 不调用 `setenforce`，不写 permissive 配置，也不要求关闭 SELinux。正式 Fedora
验收机必须以 `getenforce` 返回 `Enforcing` 运行；若策略拒绝某项访问，应修正包的 label／
policy，而不是降低系统安全状态。

## 桌面入口

desktop 文件执行 `/usr/libexec/classscribe-launcher`。launcher 调用
`systemctl --user start classscribe-core.service`，最多等待 5 秒轮询
`http://127.0.0.1:8765/healthz`，健康后才用 `xdg-open` 打开这个 loopback URL；超时明确
失败，不尝试其他主机或网络地址。

## RPM 生命周期测试

集成测试实际构建 0.1.0 和 0.1.1 两个 RPM，由独立 RPM 数据库执行 install → upgrade →
erase。为适应普通用户测试容器，测试使用 RPM 的 `--badreloc --relocate` 把 `/` 映射到
`/tmp`，而不是伪造文件复制；它核对规范路径、升级版本、卸载包文件，并用 sentinel 证明
用户数据始终保留。Fedora 实机发布验收仍需执行正常系统安装与 SELinux Enforcing 检查。
