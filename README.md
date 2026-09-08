# ClassScribe

ClassScribe 是面向 Fedora 的本地离线中／日／英语课堂转录工作台与 IBus
语音输入法。两个产品形态共享协议、配置、时间轴、模型注册表和本地数据基础设施，
但使用彼此独立的推理策略；不得把它们合并成语义含糊的单一“转录模式”。

当前的规范性产品合同见：

- [产品需求](docs/product-requirements.md)
- [术语表](docs/glossary.md)
- [需求—测试追踪矩阵](docs/requirements-traceability.md)
- [验收清单](docs/acceptance-checklist.md)
- [界面措辞规范](docs/ui-copy-guidelines.md)
- [架构决策记录](docs/adr/README.md)
- [Fedora 安装与普通用户服务](docs/fedora-installation.md)
- [模型安装与离线运行](docs/model-installation.md)

## 产品承诺

- 正式推理完全在本机执行；只有用户主动发起模型安装或更新时才允许联网。
- 课堂任务无需人工阻塞即可运行到导出；校对是可选增强，而不是流水线关卡。
- 自动采用的文字必须可追溯到模型候选或确定性的术语规则。
- 忠实转录、智能纠正和用户编辑是互不覆盖的文本层。
- 疑似重复的候选保留正文并标红待复核；没有可用候选时使用带时间范围的“无可用转录”标记，不得编造内容。

## 明确不承诺

ClassScribe **不承诺**任何录音条件下都达到零 CER/WER，也不承诺重叠语音中每个
字词都能被绝对正确地归属给说话人。厂商在其他数据集上的宣传数字不作为本机最佳
模型排名。语言模型不得为了让文本显得完整而自由改写忠实转录层。

项目按 [分阶段开发计划](docs/ClassScribe分阶段开发计划.md) 推进，每个阶段仅在其
交付物、测试与退出条件全部满足后才记录为完成。

## 当前发布状态

工程实现由 `scripts/validate_release.py` 做 fail-closed 检查。当前源码许可证、真实模型／私有
gold 验收和完整桌面矩阵的原始检查仍未通过；release owner 已对这三项实施一次精确、版本绑定的
自动阻塞 waiver。详情见
[已知限制](docs/known-limitations.md)、[风险记录](docs/risk-register.md) 和
[许可证状态](LICENSES/README.md)。

```bash
UV_CACHE_DIR=/tmp/classscribe-uv-cache uv run --offline python scripts/validate_release.py
```

当前十个发布门中七个原始工程门通过，另三项保持 `false`；有效 waiver 使命令以状态码 0 返回
`ready_with_waivers`。这不把缺失的私人验收数据、桌面实机或版权方授权伪造成成功，也不授予源码
或图标的再分发权。
