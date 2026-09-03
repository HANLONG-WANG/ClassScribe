# 故障排查

## 基础自检

```bash
uv sync --frozen
uv run classscribe-core --check-config
uv run classscribe-core --health-check
CLASSSCRIBE_DATABASE_URL=sqlite:////tmp/classscribe-check.sqlite3 uv run alembic upgrade head
uv run python scripts/check_architecture.py
uv run pytest
pnpm --dir frontend check
```

健康检查成功只说明进程和协议边界可启动，不表示某个 registry revision 已安装。

## 数据库恢复

数据库必须报告 `journal_mode=wal` 和 `foreign_keys=1`。服务异常退出后，启动扫描会把
遗留 running checkpoint 变为 retryable，并返回第一个未完成片段；不要手工把所有 job
设为 completed。迁移前备份数据库，使用显式 `CLASSSCRIBE_DATABASE_URL`，再运行
`uv run alembic check` 确认 metadata 没有未迁移差异。

浏览器提交旧 `transcript_segments.version` 时会收到 `SEGMENT_VERSION_CONFLICT`；刷新
该片段、检查 decision events 后再合并编辑，不能强制覆盖。

## API 请求被拒绝

只从 loopback 访问，并从权限 `0600` 的 XDG `api-token` 文件读取 bearer token。不要把
token 放进 URL、普通 YAML 或日志。非 loopback、缺失 token 和错误 token 分别返回稳定
错误码，不能通过关闭认证解决。

## 配置无法载入

确认 YAML 根为 mapping，`config_version: 1`，且没有 schema 之外的字段。覆盖优先级为
默认值 < 用户文件 < `CLASSSCRIBE__SECTION__KEY` 环境变量。旧版无版本文件按 v0
迁移：`server.bind` → `server.host`，`privacy.offline` → `privacy.runtime_offline`。
未来版本会被拒绝，防止静默误读。

## XDG 目录被拒绝

ClassScribe 应用目录必须是绝对路径、当前用户所有、非符号链接并采用 `0700`。修复
所有者或权限后重试；不要把 data root 指向共享、组可写或 world-readable 目录。
相对路径、`..` 和通过符号链接逃逸 data root 的 job 路径都会被拒绝。

## 时间戳不一致

检查所有边界是否使用 16 kHz 整数 sample。不要累加浮点秒；worker 局部时间必须加
请求的绝对 sample offset。显示毫秒的舍入值不能写回数据库。

## 模型或 worker 不可用

先检查用户注册表的 `enabled`、`installation.state`、revision、artifact SHA 和 worker
lock SHA。模型缺失或 hash 漂移时正式推理必须明确失败；请由用户主动运行模型安装器，
不能依赖运行时自动下载或云端 fallback。worker RPC socket 必须位于当前用户 0700 runtime
根且自身为 0600；批音频必须是显式 data root 内的只读 canonical master。

本机已主动安装 reference checkpoint 时，可运行真实模型生命周期检查：

```bash
CLASSSCRIBE_REAL_MODEL_PATH=/absolute/pinned/faster-whisper-tiny \
  uv run --offline --frozen pytest -q tests/integration/test_real_model_worker.py
```

该测试若未设置路径会 skip；它不会联网。reference 结果不得进入 auto-best 或替代语言专用
候选。RPC deadline 返回 `deadline_exceeded`，显式 cancel 返回 `cancelled`，worker crash
由 core 标记为 transport failure；不要在相同参数上无限重试。

## Wayland 快捷键

只支持 XDG Desktop Portal `GlobalShortcuts`。当前 portal companion 会创建会话、绑定快捷键并
记录后端诊断；后端不支持或快捷键冲突时请切换到 ClassScribe IBus 输入源。不要采用全局键盘
抓取作为临时替代。
