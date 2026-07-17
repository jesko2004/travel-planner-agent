# 架构与模块边界

## 运行结构

```text
Streamlit UI
    -> TravelPlannerWorkflow
        -> DeepSeekPlanner
        -> AmapMCPClient
        -> ItineraryValidator
        -> SQLiteRepository
    -> ICS exporter
```

`travel_planner` 包按职责拆分：配置与公共模型位于包根；外部服务位于 `services` 和 `mcp`；持久化位于 `storage`。任何外部响应必须先转换为公共数据模型，UI 不接触原始 MCP 输出。

## Claude Code 风格的治理映射

- 项目记忆：`CLAUDE.md -> AGENTS.md -> limitation.md`。
- 按需规则：详细架构、数据契约、工作流只由 `limitation.md` 索引。
- 专门 Agent：以工作流步骤和最小工具集合表达角色边界。
- Hooks：用启动检查、模型后解析、保存前校验和测试实现确定性门禁。
- 权限：高德仅只读；外部写入默认禁用；本地数据库写入通过仓储层。

## 依赖规则

不允许循环依赖。`models` 不依赖任何业务模块；`validator` 只依赖模型；UI 依赖公共门面，不导入 MCP 实现细节。

