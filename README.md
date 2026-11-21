# NexusGuard Fulfillment Agent

面向电商售后与履约闭环的数字员工项目。当前已实现 API、核心数据表、客服接待与订单定位、模型意图识别、大模型工具循环、后台查询、自然回复展示及多商品破损申报信息收集；售后裁决与资金闭环尚待实现。

## 技术栈

Python 3.11+、FastAPI、Pydantic Settings、SQLAlchemy 2 异步接口、PostgreSQL 16、Redis 7、Alembic、pytest、Ruff；前端使用 Vue 3、TypeScript、Vite。

## 目录

```text
src/nexusguard/
├── main.py                 # 应用工厂与资源生命周期
├── api/                    # HTTP 聚合、存活与就绪检查
├── core/                   # 配置、JSON 日志
├── domain/                 # 业务枚举与工作流状态契约
├── infrastructure/         # 异步数据库、模型网关、Redis 与 models/ 核心表
└── modules/
    ├── supervisor/         # 意图路由与复合任务编排
    ├── presales/           # 商品、库存、优惠适配
    ├── logistics/          # 物流核验、受控改址
    ├── aftersales/         # 售后事实、政策与风险评估
    ├── approvals/          # 审批、挂起与恢复
    └── payments/           # 内部资金执行边界，不暴露公共 HTTP 路由
frontend/                   # Vue 3 + TypeScript，组件、会话状态与 API 客户端
migrations/                 # 核心表和会话接待表的增量迁移
tests/                      # 单元测试和真实 PostgreSQL 并发/事务集成测试
docs/architecture.md        # 模块职责、安全边界与后续实施顺序
```

## 本地启动

后端需要 Python 3.11+、PostgreSQL 和 Redis，可使用本机服务或 Docker Compose v2。
前端需要 Node.js 22.12+（22 系列）或 24+。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres redis
alembic upgrade head
uvicorn nexusguard.main:create_app --factory --reload
```

开发 API 文档：<http://127.0.0.1:8000/docs>。

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

- `/health/live` 仅检查 API 存活，不访问外部服务。
- `/health/ready` 并行检查 PostgreSQL 与 Redis；任一不可用或超时返回 503。
- 每个响应携带 `X-Request-ID`，请求摘要日志使用 JSON，默认不记录请求正文或查询参数。
- `production` 环境隐藏 OpenAPI 与交互文档。
- 客服接待和商品详情 API 使用宿主注入的顾客身份，员工审核接口使用独立权限。

也可以容器启动整个开发环境：

```bash
docker compose build api
docker compose run --rm api alembic upgrade head
docker compose up -d
```

Compose 内的账号密码仅供本地开发，服务端口仅绑定本机。Redis 当前用于就绪检查，尚未实现缓存策略；Agent 消费 PostgreSQL Outbox；工作流真相源应放在 PostgreSQL。

## 客服会话接待

流程图、入口规则、请求示例及认证接入方式见 [客服接待流程](docs/flows/customer-reception.md)。
后端按入口展示候选订单，并返回确认按钮、历史订单选择及按订单/SKU 状态生成的建议问题。
候选订单不会直接成为咨询订单；自由输入由 `qwen3.8-flash` 识别会话动作和业务诉求，后端校验订单，歧义进入澄清。按钮动作不调用模型。

Vue 客服页面位于 `/customer-service`：选定订单后，在卡片下方展示 2～3 个建议问题，切换订单时刷新，
始终保留自由输入框。前端目录与学习说明见 [前端指南](docs/frontend.md)。

后端运行在 8000 端口时，另开终端启动前端：

```bash
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
npm run dev
```

访问 `http://127.0.0.1:5173/customer-service`，API 由 Vite 代理到后端。
本机部署时先在 `frontend/` 执行 `npm run build`，再启动或重启 FastAPI，
即可通过 `http://127.0.0.1:8000/customer-service` 访问构建后的页面。
Dockerfile 会自动完成前端构建；纯 API 开发不要求预先构建前端。

会话、消息及回复保存到 PostgreSQL；Outbox 消费者执行意图识别与受控查询。先执行
`alembic upgrade head` 应用到 `0010_refund_application_scope`，再另开终端启动后台进程：

```bash
python -m nexusguard.modules.supervisor.agent_worker
```

页面显示排队、处理、待选订单和回复，并可恢复 `session_id` 对应的历史记录。
顾客填写诉求并提交破损售后申请后，审核员在 `/damage-review` 逐项核验材料、保存整份申请结论。客服会话在人工处理完成后显示结果。审核页面使用宿主注入的员工身份。详见 [破损售后申请](docs/damage-review.md)。
尚未实现登录，未登录时页面提示先登录。
调用前由认证适配设置 `request.state.customer_identity`，不要用浏览器自报的用户 ID 替代认证。

## 配置

读取 `.env` 或 `NEXUSGUARD_` 前缀的环境变量。`DATABASE_URL` 和 `REDIS_URL` 必填；数据库连接必须使用 `postgresql+asyncpg`。完整本地示例见 `.env.example`。

其他可选项：`ENVIRONMENT`、`LOG_LEVEL`、`DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`、`DEPENDENCY_TIMEOUT_SECONDS`。连接池限制按进程计算，多 Worker 部署时需结合数据库连接总额调整。

## 模型网关

实际使用两个可配置的调用档位：`fast` 使用 `qwen3.8-flash`，`reasoning` 使用 `qwen4-max`。前者负责意图识别，后者负责对话、受限工具选择和破损信息提取；不代表已完成全面模型质量评测。

将 API Key 写入本地 `.env` 的 `NEXUSGUARD_LLM_API_KEY`。`.env` 已被 Git 和 Docker 构建上下文忽略，建议权限为 `600`；示例文件不含真实 Key。

```bash
python -m nexusguard.tools.smoke_llm
```

该命令对每个模型各发送一次简短测试请求，可能产生少量费用；不发送订单数据，不打印凭据、请求正文或模型输出，仅报告状态、耗时和 Token 用量。测试不会在服务启动、健康检查或 CI 中自动运行。

网关提供异步连接复用、每进程并发上限、包含排队时间的超时、输出 Token 上限与响应校验。默认不重试、不自动切换模型；错误仅返回稳定错误码和 HTTP 状态，不透传上游响应。当前不支持供应商原生 `tool_calls`；Agent 使用严格 JSON 动作协议，只能调用后端白名单工具。破损草稿变更由后端校验并提交，没有资金操作权限。

配置项均使用 `NEXUSGUARD_` 前缀：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_FAST_MODEL`、`LLM_REASONING_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_CONCURRENCY`、`LLM_MAX_OUTPUT_TOKENS`、`AGENT_MAX_STEPS`。未配置 Key 时 API 仍可启动；显式调用网关时返回未配置错误。

Compose 默认只启动基础设施骨架；如需容器内使用模型，应在部署配置中通过环境变量或密钥管理注入模型配置，避免构建进镜像。

## 检查

```bash
ruff check .
ruff format --check .
pytest
```

单元测试无需真实 PostgreSQL/Redis；就绪检查和模型调用使用模拟探针。数据库集成测试需要独立空数据库，名称必须以 `nexusguard_test_` 开头；测试会在该库执行迁移升级与回滚，不应指向开发或生产库。

```bash
pytest -m 'not integration'
NEXUSGUARD_TEST_DATABASE_URL=postgresql+asyncpg://nexusguard:nexusguard_dev@localhost:5432/nexusguard_test_persistence pytest -m integration
```

不设置测试连接时，数据库测试明确跳过；CI 会启动 PostgreSQL 16 执行这些测试。支付适配与对账测试待执行服务实现。

整体场景及本次执行结果见 [整体回归记录](docs/verification.md)。

## 数据库迁移

禁止应用启动时自动 `create_all`。首版迁移 `0001_core` 已包含 21 张核心表、跨租户/订单关联约束、退款额度、幂等唯一键和追加写历史保护。

```bash
alembic upgrade head
alembic check
```

后续模型变更通过 `alembic revision --autogenerate -m "describe change"` 生成新版本，审核后执行。回滚会删除对应表和数据，只在明确的测试或回滚流程中使用。

完整字段和保证范围见 [数据模型文档](docs/data-model.md)。当前数据库约束不替代身份认证、业务规则或真实支付执行。

## 下一步

破损售后流程覆盖提交、人工处理、退款风控、主管审批和本地模拟退款。接入真实支付渠道需要实现支付适配器。详见 [模拟退款演示](docs/damage-review.md#模拟退款)。

已提供意图识别、查询和破损信息收集 Agent；支付、身份认证、政策裁决或审批执行仍未接入，不具备生产资金处理能力。详见 [客服 Agent 实现](docs/customer-agent.md)。
