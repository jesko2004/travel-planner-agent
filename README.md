# 我的国内旅行助手

面向个人、本地运行的中国大陆旅行规划应用。它使用 DeepSeek 生成结构化行程，使用官方高德 MCP 核实地点、天气、距离与路线，并把非敏感偏好和历史行程保存在本地 SQLite。

## 安全边界

- 酒店候选来自高德 POI，不代表实时价格或库存。
- 携程、同程、飞猪和美团按钮只打开官方平台，由用户自行搜索、核价和下单。
- 应用不会登录第三方账号、抓取网页、绕过验证码、创建订单或付款。
- 不要在应用中输入身份证、银行卡、支付凭证或平台密码。

所有开发约束以 [limitation.md](limitation.md) 为准。项目提供 `AGENTS.md` 和 `CLAUDE.md` 入口，兼容通用编码 Agent 与 Claude Code 的项目记忆加载方式。

## 主要能力

- 中文旅行需求表单与本地默认偏好。
- DeepSeek `deepseek-chat` 结构化行程草案。
- 官方 `@amap/amap-maps-mcp-server` 地点、天气和市内路线验证。
- 真实路线补全、时间重排、预算合计和确定性校验。
- 高德酒店 POI 候选及四个平台的人工搜索入口。
- 行程验证状态、来源和查询时间展示。
- 本地 SQLite 历史记录、复制、删除和锁定活动后重新规划。
- 按活动时间导出的 ICS 日历。
- 高德故障时生成明确标注的未验证草案。

## 环境要求

- Python 3.10 或更高版本。
- Node.js 与 npm。高德官方文档建议 Node.js 22.14.0 或更高版本。
- DeepSeek API Key。
- 高德开放平台 Web 服务 Key。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制配置示例：

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

编辑 `.streamlit/secrets.toml`：

```toml
DEEPSEEK_API_KEY = "你的 DeepSeek Key"
AMAP_MAPS_API_KEY = "你的高德 Key"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
TRAVEL_PLANNER_DB = "data/travel_planner.db"
MCP_TIMEOUT_SECONDS = "60"
```

也可以使用同名环境变量。真实密钥文件已加入 `.gitignore`。

## 运行

```powershell
streamlit run app.py
```

侧边栏会分别检查 DeepSeek、高德 Key、Node/npm 和 SQLite。高德 Key 或 Node 缺失时仍可生成未验证草案，但不会显示精确路线、天气或完成状态；DeepSeek 不可用时禁止生成。

## 在 VS Code 中修改

1. 使用“文件 → 打开文件夹”打开本项目根目录。
2. 安装工作区推荐的 Python、Pylance、GitHub Pull Requests 和 GitLens 扩展。
3. 在“终端 → 运行任务”中依次运行“项目：安装依赖”“项目：运行测试”“项目：运行 Streamlit”。
4. 新方案先复制 [修改方案模板](docs/change-plan-template.md)，或上传 GitHub 后创建“功能或修改方案”Issue。
5. 详细分支、提交和 PR 流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 上传 GitHub

项目已包含 `.gitignore`、GitHub Actions、Issue 模板和 Pull Request 模板。首次上传可以在 VS Code 的“源代码管理”面板中选择“发布到 GitHub”，建议先创建为 Private 仓库。

命令行方式：

```powershell
git add .
git commit -m "chore: 初始化国内私人旅行助手框架"
git branch -M main
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

提交前确认 `.streamlit/secrets.toml`、`.env`、数据库和 `.venv` 没有进入暂存区。

## 测试

```powershell
pytest -q
```

单元测试不访问 DeepSeek 或高德，覆盖数据校验、酒店入口、路线完整性、预算、SQLite 和分时 ICS。真实北京三日行属于集成验收，需要有效 Key 和网络连接。

## 目录

```text
app.py                       Streamlit 页面
limitation.md                唯一规范索引
AGENTS.md / CLAUDE.md        Agent 入口
docs/                        架构、数据契约与工作流
travel_planner/
  config.py                  配置与密钥加载
  models.py                  公共结构化数据模型
  workflow.py                分阶段编排
  mcp/amap_client.py         官方高德 MCP 客户端
  services/                  DeepSeek、校验、酒店入口、ICS
  storage/database.py        SQLite 仓储
tests/                       不访问外部服务的测试
```

## 设计来源

Agent 治理参考了 Claude Code 官方的项目记忆、子 Agent 隔离与 Hooks 思路：

- [项目记忆与 CLAUDE.md](https://code.claude.com/docs/en/memory)
- [自定义子 Agent](https://code.claude.com/docs/en/sub-agents)
- [Hooks 生命周期](https://code.claude.com/docs/en/hooks-guide)

这些机制在本项目中被转换为短入口文件、最小工具权限的分阶段角色，以及由 Python 校验器执行的确定性检查点；应用运行时并不依赖 Claude Code。
