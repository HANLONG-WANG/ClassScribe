# 已知限制

## 当前发布阻塞

- 没有用户提供的中／日／英私有人工 gold、90 分钟真实课堂、每语言 50～100 条 IBus 近讲和
  1/2/5+ speaker 数据，因此 Phase 12 为 `incomplete`，不能生成 production `auto_best`。
- 当前验收机/XDG 数据区没有可供本次签核使用的固定生产 checkpoint（含 gated 私有权重）；真实
  worker 集成测试按设计 skip，不能声称本机 CER/WER、DER、RTF、p95、VRAM 或 OOM 结论。
- 本机 NVIDIA userspace/driver 不匹配，CUDA 实测不可用。
- GNOME/KDE × Wayland/X11 × GTK/Qt/Firefox/Chromium/Electron/terminal/LibreOffice 的完整矩阵
  尚未执行，Portal 和 IBus 兼容报告为 `incomplete`。
- ClassScribe 源码和自制 SVG 图标尚无版权方选定许可证；当前 `NOASSERTION` 明确阻止公开 RPM
  签名与再分发。

## 产品边界

- 自动语音识别会出错；重叠、远场、噪声、口音和领域术语仍需校对。产品不承诺零 CER/WER。
- speaker ID 是任务内匿名标签，不是生物身份识别。窗口 stitching 可能产生 uncertain 新 speaker。
- `auto_mixed` 使用滞回避免抖动，不保证每个外语术语都切换语言；专名通常保留在主语言句段。
- 精细对齐只处理短、质量通过的片段；失败时保留真实区间的 coarse timing。
- 模型原始 confidence 不能跨模型比较；只有相同 revision 和私有 gold 拟合的 calibration 才能用于
  统一概率。
- gated 模型需用户接受上游条件。上游撤销访问或变更条款时，已固定 revision 也不自动获得授权。
- RPM 不含模型权重或 worker 虚拟环境；这些必须在用户明确操作中按各自 frozen lock 建立并验证。

## 默认禁用候选

- FireRedASR2-LLM：12 GB 目标 GPU 上没有验证低内存或量化 runtime。
- Voxtral Mini 4B Realtime：官方 BF16 artifact 超出 12 GB 稳定默认预算，待量化实测。
- VibeVoice ASR Streaming：发布新且参数元数据/稳定性、重复、显存和说话人归属尚未本机验收。
- Fun-ASR-Nano：只供专家测试，已知必须严格防循环并复核日语标点。
- Whisper Tiny reference：只验证真实 worker 生命周期，不参加 production auto-best。

这些限制由 `classscribe-release-check`、注册表 enabled/disable_reason 和 Phase 12 acceptance 共同
执行，不能靠 UI 隐藏。
