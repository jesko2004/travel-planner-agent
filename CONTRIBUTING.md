# 修改与协作指南

## 在 VS Code 中开始

1. 安装 Python 3.10+、Node.js 22.14+、Git 和 VS Code。
2. 用 VS Code 打开项目根目录。
3. 接受推荐扩展。
4. 运行任务“项目：安装依赖”。
5. 复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 并填写本地 Key。
6. 运行“项目：运行测试”和“项目：运行 Streamlit”。

## 修改流程

1. 完整阅读 `limitation.md`。
2. 在 GitHub 创建“功能或修改方案”Issue，或复制 `docs/change-plan-template.md`。
3. 对触及 MCP、数据源、外部写操作和敏感数据的方案，先修改规范并确认，再改代码。
4. 从 `main` 创建 `feature/<简短名称>` 或 `fix/<简短名称>` 分支。
5. 保持结构化数据、验证证据和降级规则同步更新。
6. 运行语法检查与单元测试。
7. 推送分支并按 PR 模板提交；CI 通过后再合并。

## 提交建议

使用清晰的小提交，例如：

- `docs: 补充酒店数据来源约束`
- `feat: 增加高德公交路线展示`
- `fix: 修复跨日活动校验`
- `test: 覆盖 SQLite 保存失败降级`

禁止提交 `.streamlit/secrets.toml`、`.env`、数据库、虚拟环境或任何真实账号凭证。

