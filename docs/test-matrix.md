# 全层测试与发布验收矩阵

| 层 | 自动证据 | 发布机／人工证据 |
|---|---|---|
| 单元 | sample/ms 90 分钟无漂移、VAD merge/overlap/hard split、LID 滞回、三语 normalizer、标点字符不变量、loop、reading、confusion/provenance、SRT/VTT、迁移/乐观锁 | 无 |
| 属性 | 任意合法区间完整覆盖、最终时间边界、Unicode 标点不改字、非 overlap token 不误删 | 无 |
| Worker 合同 | 11 worker capability/lifecycle、deadline/cancel、空/静音/极短/非法语言/超长/Unicode/标点协议拒绝与 unload/crash | 每个已安装固定 revision 的真实短音频、显存释放 |
| 集成 | MOSS→ASR→fallback→align→export、pause/resume/restart/crash、IBus 安全抢占、missing/offline/disk/VRAM failure | 中/日/英至少一个真实已安装模型 |
| E2E | 90 分钟 sample 结构测试、5 分钟 IBus token 完整性、浏览器工作台 | 90 分钟真实课堂；1/2/5+ speaker；中英/日英/三语；静音/音乐/中断；5 分钟真实听写 |
| 桌面 | IBus bridge 与八类 client 合同、Portal fallback | Wayland GNOME/KDE、X11 GNOME/KDE 的八类真实应用矩阵 |
| 回归 | `tests/golden/regressions.v1.json` 六类固定 guard | 模型/依赖升级后对对应真实音频重跑 |

发布机必须同时保存：测试命令与 commit、RPM/lock hash、模型 revision/artifact hash、gold manifest
hash、硬件/驱动、decode 参数、完整 benchmark report 和桌面 evidence。缺一项即保持 incomplete，
不得把自动算法 fixture 标成真实模型或真实桌面结果。

结构化填写入口为 `benchmarks/manifests/test-evidence.template.json`；Worker 的精确集合、case 集合和
显存释放容差由 `config/worker-contract-matrix.v1.json` 冻结。简单 `true` 不构成发布证据。
