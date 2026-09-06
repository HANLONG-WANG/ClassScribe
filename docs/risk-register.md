# 发布风险关闭记录

状态含义：`controlled` 表示代码控制和回归证据存在；`release-blocked` 表示控制已实现但仍缺本机
验收；`waived-open` 表示原始风险仍未解除，但 release owner 已明确承担并只跳过自动阻塞；`disabled`
表示候选能力保留但默认不可选。关闭日期均为 2026-09-04。

| 风险 | 控制 | 证据 | 状态 |
|---|---|---|---|
| 公开榜单不代表真实课堂 | 私有 gold，语言×课堂/IBus 分开校准与排名 | benchmark schema/runner/ranking + Phase 12 fail-closed report | waived-open（原始 Phase 12 仍 incomplete） |
| MOSS 90 分钟超 12 GB | 12 分钟自适应窗口、重叠去重、逐窗落盘、OOM 有限降级 | structure pipeline、90 分钟窗口回归 | release-blocked（真实 VRAM 未测） |
| 多模型依赖冲突 | 每 worker 独立 pyproject/uv.lock/进程 | 11 worker contract matrix 与离线 lock check | controlled |
| IBus 与课堂争 GPU | 0/10/20/30/40 租约、checkpoint 安全抢占、精确恢复 | scheduler 与课堂/IBus preemption tests | controlled |
| IBus 预加载模型无 owner／超配 | core supervisor、固定 revision/frozen env、VRAM budget、原子同 UID 清单、idle-boundary 路由 | resident supervisor/manifest/router tests | controlled（真实 VRAM 仍由 Phase 12 验收） |
| 生成循环／静音幻觉 | 长度、n-gram/短循环、silence/coverage/script/time 门和换模型 | golden regressions + quality tests | controlled |
| 日语标点不稳定 | boundary 投射、声学 cue、严格非标点字符不变 | punctuation property/regression tests | controlled |
| 错误文字被强制对齐 | 文字/覆盖/循环/语言/时长门，失败保留粗时 | alignment gate/repository tests | controlled |
| speaker stitching 跳 ID | embedding、重叠约束、uncertain 新 speaker、exclusive 回退 | structure/stitching tests | release-blocked（真实 5+ 人未测） |
| LID 过切 | 主 ASR+FireRedLID 融合、0.80 连续两窗滞回、自然边界切换 | audio + IBus LID tests | controlled |
| 模型更新破坏行为 | revision lock、逐文件 SHA、benchmark 失效、升级/回滚 | model manager/registry/release-check tests | controlled |
| `trust_remote_code` 供应链 | 固定提交和代码哈希、独立 worker、Bubblewrap 无网络、离线 env | manifest/sandbox/model manager tests | controlled |
| Wayland 全局快捷键限制 | Portal GlobalShortcuts，IBus 输入源兜底 | portal contract + desktop evidence schema | waived-open（桌面矩阵未完成） |
| IBus 边界重复／漏词 | sample overlap、token time、stable prefix、无固定字符删除 | 固定 5 分钟 300-token 回归 | controlled |
| 显式模型安装被 systemd 网络封死或运行期隐式下载 | core 保留唯一确认式 HTTPS 下载入口；worker unshare network；runtime 只 resolve 已 provision 环境 | installer/systemd/sandbox/release-check tests | controlled |
| 源码／自制图标再分发权不明 | `NOASSERTION` inventory 和 fail-closed source-license gate | release checker | waived-open（waiver 不授予权利，仍需版权方决策） |

`waived-open` 项没有被文字改为 closed：原始机器检查和结构化报告继续失败，仅自动发布阻塞由
`release/release-waivers.v1.json` 精确覆盖。真正解除仍须版权方授权或使用同一 pinned revision、同一
机器事实和结构化证据重新运行 Phase 12／桌面验证。实验候选 Voxtral、VibeVoice、FunASR 和
FireRedASR2-LLM 保持 disabled，直到各自 VRAM、循环、延迟和质量门通过。
