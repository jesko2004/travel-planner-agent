# 数据契约

所有时间采用带 `Asia/Shanghai` 时区的 ISO 8601，日期采用 `YYYY-MM-DD`，货币固定 CNY。高德坐标必须显式标记为 `GCJ-02`，不得与其他坐标系静默混用。外部输入和输出模型均拒绝未声明字段。

## 输入边界

`TripRequest` 是完成结构和敏感信息校验后的内部请求：

- 出发城市、目的地长度为 1–64 个字符。
- 偏好、必去和避开列表最多 30 项，每项 1–80 个字符。
- 用户手工录入的城际交通文本最多 1000 个字符。
- 儿童年龄为 0–17 岁；儿童数为 0 时年龄列表必须为空，提供年龄时数量必须与儿童数一致。
- 总预算为 100–1,000,000 元；每晚住宿预算为 0–100,000 元且下限不得高于上限。
- 自由文本中的 API Key、私钥、Cookie、密码、身份证、银行卡、完整手机号和明确标注的家庭地址会在外部调用与保存前被拒绝；运行时已加载的 DeepSeek/高德 Key 还会按精确值匹配，因此裸 Key 也不能进入业务数据。
- 系统或供应商机器引用字段（`id`、`*_id`、`*_ids`）仅在值符合受控 ASCII 标识符格式时，才豁免无标签数字形态的手机号、身份证和银行卡启发式，避免随机 UUID/POI ID 误报；包含空白、中文或说明标签的值仍执行完整扫描，明确标注的敏感内容、密钥、私钥、Cookie 和密码继续拒绝。旧库合法 JSON 必须先解析后按字段执行同一规则，且在备份前完成扫描。

`SensitiveDataFinding` 只保存字段路径和敏感类别；`SensitiveDataError` 的对外文本不得保留或回显命中的原文。

## 外部最小化对象

- `PlanningPayload`：发送给 DeepSeek 的城市、日期、人数、预算、节奏、交通、偏好、时间窗等最少规划字段。
- `ResearchQuery`：发送给高德的目的地、日期和 POI 检索所需的最少字段。
- 候选 POI 和锁定活动以受控子对象附加；API Key、数据库内容、完整历史与内部验证状态不进入 DTO。

禁止把完整 `TripRequest.model_dump_json()` 直接发送给外部服务。任何模型或 MCP 输出中的 `status`/`verified` 都不构成可信状态。

## 证据与状态

`EvidenceKind` 区分 `poi_location`、`poi_operating_status`、`current_weather`、`weather_forecast`、`route` 和 `hotel_location`。`SourceEvidence` 至少包含来源、工具名、证据类型、带时区的 `checked_at`/`expires_at`、实时性、必要原始标识符、坐标系和当前状态。

默认有效期：

| 证据类型 | 默认有效期 |
|---|---:|
| POI 位置 | 30 天 |
| POI 营业状态 | 24 小时 |
| 当前天气 | 3 小时 |
| 路线 | 6 小时 |
| 酒店位置 | 7 天 |
| 天气预报 | 必须使用供应商返回的期限 |

`checked_at` 和 `expires_at` 的精确边界由注入的北京时间判断：`now >= expires_at` 即过期。证据状态由策略代码计算，模型/MCP 业务 DTO 不允许直接提升它。

行程状态优先级固定为：

1. 必需阶段未完成：`draft`。
2. 存在来源、结构或业务错误：`unverified`。
3. 唯一失败原因是必需证据过期：`expired`。
4. 必需阶段全部完成、全部证据当前有效且没有错误：`verified`。

当前尚未实现真实高德工具调用记录适配器，因此运行时查询结果即使结构完整也只能是 `unverified`，不能仅依据模型自报状态成为 `verified`。

## 业务对象

- `UserProfile`：长期非敏感默认值，包含稳定 `profile_id`、`schema_version=1`、`created_at`、`updated_at`。
- `PoiCandidate`：唯一 POI ID、名称、地址、有效经纬度、类别与位置证据。
- `HotelCandidate`：酒店 POI、位置说明、平台搜索入口与强制确认提示。
- `Activity`：稳定 ID、日期、开始/结束时间、POI、估算费用与锁定状态。
- `RouteLeg`：精确相邻活动 ID、方式、距离、时长、摘要、导航入口与路线证据。
- `Itinerary`：稳定行程 ID、`schema_version=1`、唯一 `run_id`、非敏感请求摘要、创建/更新时间、请求、每日计划、酒店、天气、预算、问题和派生状态。
- `ValidationContext`：固定当前时间、目的地确认状态、批准 POI ID、锁定活动快照和必需阶段完成状态。
- `LockedActivitySnapshot`：重新规划前锁定活动的 ID、日期、时间、POI ID 和锁定状态。
- `ValidationIssue`：级别、代码、信息及可选日期/活动关联，不保存敏感输入原文。

模型输出只允许提交 `ItineraryDraft` 草案字段；证据、验证状态、预算合计和路线信息由程序生成并校验。

## 持久化兼容

SQLite 业务 schema 与 `PRAGMA user_version=1` 同步。旧版 `user_version=0` 数据库会先解析并扫描 JSON，再使用 SQLite backup API 在本地 `backups/` 创建 `.db` 备份，随后在事务中执行显式 `0→1` 迁移。迁移后的对象必须逐 ID 回读；迁移或回读失败必须回滚并保留原库。应用只保留会话态结果并显示“未保存”，不得静默创建内存库或改写其他路径。

已保存的状态只是上次校验结果。历史列表与详情回读时必须使用当前北京时间重新校验证据有效期，并采用只降不升策略；超过 TTL 的旧 `verified` 会成为 `expired`，不能因回读而获得新的可信度。

