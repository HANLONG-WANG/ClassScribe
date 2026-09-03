# 本机 OpenAPI 合同

运行 core 后，交互式文档位于 `http://127.0.0.1:8765/docs`，机器合同位于
`http://127.0.0.1:8765/openapi.json`。两者只在 loopback 提供。`/healthz` 无需认证；其他 API
使用 `/api/v1` 前缀并要求 `Authorization: Bearer <token>`。POST/PUT/PATCH/DELETE 还要求
`X-CSRF-Token`，不接受 query/cookie 代替。

## 资源组

| 资源 | 主要操作 | 不变量 |
|---|---|---|
| recordings | 上传、元数据、受控媒体读取 | 文件名不能是路径；媒体只以 UUID 解析 |
| jobs | 创建、状态、SSE、暂停、继续、取消、重试 | checkpoint 持久化；SSE 是有界 replay |
| transcripts/segments | 分层文本、候选、provenance、拆分、合并、撤销、重做 | int64 absolute sample；optimistic version |
| models | 列表、安装计划、二次确认安装、验证、删除 revision、回滚 | 完整 commit、manifest SHA、许可证/条款、用户确认、沙箱离线健康检查 |
| profiles | 查询、应用 benchmark 排名、回滚 | 只接受 production gold 的精确 passed 顺序 |
| glossaries | CRUD、材料导入、term 更新 | 自动建议低权重；确认和来源明确 |
| exports | 六格式生成、认证下载 | 原子 0600；final token time；文本层显式 |
| benchmarks | 创建、记录、报告、应用排名 | revision/manifest/split/provenance 绑定 |
| settings/diagnostics/ibus | 设置、系统快照、daemon/portal 状态 | 不返回正文、PCM、凭证或用户绝对路径 |

## 错误、版本与文件

领域错误统一为 `{"error":{"code":"...","detail":"..."}}`，受控 ID 不存在返回 404，状态／
完整性冲突返回 409，认证错误返回 401/403。API 版本只通过路径推进；破坏性变更必须新增版本而非
静默更改 v1。上传和下载不能接收任意服务器路径。OpenAPI surface 由
`tests/integration/test_api_v1.py` 冻结，新增／删除 endpoint 必须同步测试和本文。

模型安装的写接口为：

- `POST /api/v1/models/{model_id}/install`：接收 Manifest v1，只生成十分钟、一次性的安装计划；
- `POST /api/v1/models/{model_id}/install/confirm`：提交确认 token、健康录音 UUID、健康语言、可选
  参考文本与条款接受位，成功后才返回安装 revision、aggregate SHA 和真实健康结果；
- `POST /api/v1/models/{model_id}/verify`、`DELETE /api/v1/models/{model_id}?revision=...` 与
  `POST /api/v1/models/{model_id}/rollback?revision=...`：分别复验、删除非活动 revision 和回滚。

`GET /api/v1/diagnostics/bundle` 返回只含脱敏 `diagnostics.json` 的 zip；它与诊断页使用同一快照和
认证边界，不包含音频、正文、preedit、token、凭证或用户绝对路径。

`/openapi.json` 只描述 HTTP 外壳；worker MessagePack RPC 另见 `worker-protocol.md`，两者不能共享
未版本化的内部对象。
