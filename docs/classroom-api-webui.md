# 课堂流水线、API 与 WebUI

## 自动流水线

`classscribe.classroom.ClassroomPipeline` 是课堂任务唯一编排器。全局 checkpoint 依次覆盖
上传校验、canonical master、QC、VAD、LID、MOSS 结构和自动导出；结构阶段产生的每个活动
句段再获得主 ASR、质量与复核、术语、严格标点、精细对齐和最终验证 checkpoint。checkpoint
由数据库状态机以短事务运行，参数哈希、尝试次数和片段 ID 都持久化，因此重启、暂停、取消、
整任务重试和单片段重试不会回滚已经完成的片段。

`ComposableStageRunner` 只负责把 checkpoint 交给阶段 4～9 已冻结的实现；它不复制音频、选模、
质量、术语、标点或时间逻辑。找不到 handler 是明确失败，不会把空操作记为完成。IBus 抢占通过
`SafeBoundaryPause` 只在 checkpoint/句段安全边界生效。`PipelineEventBroker` 保存每任务有界的
可重放事件，SSE 使用单调 event ID 和 `Last-Event-ID` 续传。

## 本地 API

FastAPI 的版本化接口位于 `/api/v1`，OpenAPI 由同一路由直接生成。接口分为：

- 录音上传、受保护媒体读取、作业状态/SSE、暂停、继续、取消和片段重试；
- 转录、四层文本、候选、provenance、说话人显示名、拆分/合并、撤销/重做；
- 模型安装披露、验证、删除、回滚，以及由已完成本机 benchmark 门控的 profile；
- 设置、课程词典、材料和术语；
- 六格式、三文本层、逐句/可读段落导出，以及 benchmark 运行/结果/排名应用。
- `GET /api/v1/ibus/status` 汇合 dictationd 状态机、配置、实际预加载模型与 Portal
  GlobalShortcuts 诊断；该读接口与其他 `/api/` 一样要求 bearer。

API 只接受 UUID 资源 ID。上传的显示文件名不能包含路径，下载路径只由数据库记录和 XDG
受限根反查；任何任意系统路径、symlink 或根外解析都会被拒绝。错误使用稳定的机器码响应，
不会回传物理文件路径。所有 `/api/` 请求要求 bearer，所有 POST/PUT/PATCH/DELETE 还要求与
token 常量时间匹配的 `X-ClassScribe-CSRF-Token`。WebUI 首页只在运行时响应中注入 bearer，
不会把它写入 URL 或静态构建产物。导出先在同目录原子写入，再登记 artifact。

## Web 工作台

React/TypeScript strict 前端使用 TanStack Query 管理服务器状态、Zustand 管理页面与校对状态、
WaveSurfer.js 播放受认证媒体，并用 fetch-based SSE 携带 bearer。上传页完整提交语言、词典、
说话人数、自动/手动模型、快速/平衡/最高精度/严格单模型和输出选择，创建后自动进入任务页。

任务页显示真实 checkpoint、模型、片段、RTF、显存和安全边界暂停。转录页同步波形、说话人/
语言轨和真实 sample 范围，支持 raw/faithful/smart/user、低置信筛选、候选/质量/provenance、
跳转播放、片段重跑、说话人改名、700 ms 乐观锁自动保存、撤销/重做和审计。模型、词典、导出、
设置/诊断和 IBus 页面均使用真实 API 数据；自动最佳只能采用已完成 benchmark。
IBus 页面按一秒轮询实时状态，区分 dedicated input source 与 Portal fallback，并显示 accuracy
可能的模型等待；前端不直接连接 dictationd socket，也不复制语言或分块决策。

## 验证

`tests/integration/test_api_v1.py` 核对完整 OpenAPI surface、安全、上传到作业、转录 provenance、
并发编辑、候选采用、词典/材料、设置、模型、导出与 benchmark。`tests/unit/test_classroom_pipeline.py`
覆盖多片段 checkpoint、重入、恢复、片段重试、自动导出和 IBus 安全抢占。前端 Vitest 覆盖主
交互，Playwright 用真实 Chromium 实际提交 WAV、创建任务、打开转录稿、自动保存校对、创建
词典与术语、调用模型健康验证并生成导出；另验证 daemon 诊断失败时 IBus 安全降级。生产构建、
TypeScript、ESLint 和 Prettier 均属于阶段门。
