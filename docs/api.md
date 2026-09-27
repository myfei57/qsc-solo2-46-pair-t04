# CraneFleet 对外 API 版本与错误说明

## 1. 版本规则

| 请求路径 | 解析版本 | 说明 |
| --- | --- | --- |
| `/api/...`（不带版本前缀） | v1 | **兼容路径**，等价于 `/api/v1/...`，老客户端无需任何改动 |
| `/api/v1/...` | v1 | 冻结契约：字段集固定，服务端新增字段不会出现在 v1 响应中 |
| `/api/v2/...` | v2 | 当前版本：完整视图、结构化错误、严格参数校验 |
| `/api/vN/...`（不支持的版本号） | — | 返回 404，错误码 `unknown_version`，`details.supported_versions` 列出支持的版本 |

## 2. v1 冻结策略（为什么改字段不再搞挂老客户端）

- v1 的每个路由都有一份**字段白名单**（见 `cranefleet/versions.py`）。
- 服务端在返回前按白名单投影：白名单之外的字段一律不会出现在 v1 响应里。
- 以后任何新增字段只加在当前版本上，v1 客户端永远收不到陌生字段，也就不会因为多一个字段而整页解析失败。
- 测试对 v1 的顶层字段集做了硬编码快照断言（`tests/test_versioning.py` 中的 `FROZEN_KEYS`），
  若有人误改 v1 契约，测试会立即失败。
- 兼容性边界：v1 保留旧的字符串错误体和宽松的参数处理（例如非法数字按 `0.0` 处理）；
  这两类行为的收紧只发生在 v2。

## 3. 响应封装

### 成功

v1（含裸路径）：

```json
{ "ok": true, "data": { } }
```

v2：

```json
{ "ok": true, "api_version": 2, "data": { } }
```

### 失败

v1（含裸路径），与历史完全一致：

```json
{ "ok": false, "error": "crane_id required" }
```

v2，错误按类区分，`code` 供程序判断，`message` 仅供展示，`details` 可选：

```json
{
  "ok": false,
  "api_version": 2,
  "error": {
    "code": "missing_field",
    "message": "crane_id required",
    "details": { "field": "crane_id" }
  }
}
```

## 4. 错误码与 HTTP 状态

| HTTP | code | 含义 | 客户端处置建议 |
| --- | --- | --- | --- |
| 400 | `invalid_json` | 请求体不是合法 JSON（或不是 JSON 对象） | 检查请求体序列化与 `Content-Type` |
| 400 | `missing_field` | 缺少必填字段 | 按 `details.field` 补全 |
| 400 | `invalid_field` | 字段值非法（类型不对、取值不允许等） | 按 `details.field` / `details.value` 修正 |
| 404 | `not_found` | 路由不存在，或引用的资源不存在 | 核对路径与资源 ID |
| 404 | `unknown_version` | 请求了服务端不支持的 API 版本 | 查 `details.supported_versions` |
| 409 | `conflict` | 请求本身合法，但当前设备状态不允许（互锁/闩锁/忙闲/未确认等） | 读取当前状态后重试或先解除互锁 |
| 500 | `internal` | 服务端内部故障（如持久化失败） | 可安全重试；持续出现请联系服务端 |

错误分类遵循一条原则：**400 类是客户端可以通过修改请求解决的；409 类请求没写错，
是现场状态不满足条件，不要改请求，要等状态变化**。

## 5. 升级到 v2 指南

1. 把所有请求路径由 `/api/...` 改为 `/api/v2/...`。
2. 错误处理从判断 `error` 字符串改为判断 `error.code`，不要依赖 `message` 文本。
3. v2 对参数严格校验：缺必填字段返回 `missing_field`，值非法返回 `invalid_field`
   （v1 下这类情况可能被静默当作默认值，例如非数字被当作 `0.0`）。
4. v2 数据字段是 v1 的超集（当前 v2 新增了 `state.meta` 块，包含 `api_version`
   与 `generated_at`）。以后新字段继续只进当前版本。

新增下一个大版本（v3）时，v2 的字段集同样会冻结，已接入 v2 的客户端不会被再次影响。
