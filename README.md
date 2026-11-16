# NexusGuard Fulfillment Agent

面向电商售后与履约闭环的数字员工项目。当前版本是工程基础骨架，包含可启动的 API、基础设施连接、健康检查和状态流转契约；业务闭环尚待实现。

## 技术栈

Python 3.11+、FastAPI、Pydantic Settings、SQLAlchemy 2 异步接口、PostgreSQL 16、Redis 7、Alembic、pytest、Ruff。

## 目录

```text
src/nexusguard/
├── main.py                 # 应用工厂与资源生命周期
├── api/                    # HTTP 聚合、存活与就绪检查
├── core/                   # 配置、JSON 日志
├── domain/                 # 业务枚举与工作流状态契约
├── infrastructure/         # 异步数据库与 Redis 客户端
└── modules/
    ├── supervisor/         # 意图路由与复合任务编排
    ├── presales/           # 商品、库存、优惠适配
    ├── logistics/          # 物流核验、受控改址
    ├── aftersales/         # 售后事实、政策与风险评估
    ├── approvals/          # 审批、挂起与恢复
    └── payments/           # 内部资金执行边界，不暴露公共 HTTP 路由
migrations/                 # Alembic 入口，尚无业务表迁移
tests/                      # 工程冒烟与状态图安全边界测试
docs/architecture.md        # 模块职责、安全边界与后续实施顺序
```

## 本地启动

需要 Python 3.11+ 和 Docker Compose v2。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
docker compose up -d postgres redis
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
- 当前没有业务 API。各业务模块的 Router 是预留入口，添加身份认证与授权后再注册真实接口。

也可以容器启动整个开发环境：

```bash
docker compose up --build -d
```

Compose 内的账号密码仅供本地开发，服务端口仅绑定本机。Redis 当前用于就绪检查，尚未实现缓存策略或消息消费；工作流真相源应放在 PostgreSQL。

## 配置

读取 `.env` 或 `NEXUSGUARD_` 前缀的环境变量。`DATABASE_URL` 和 `REDIS_URL` 必填；数据库连接必须使用 `postgresql+asyncpg`。完整本地示例见 `.env.example`。

其他可选项：`ENVIRONMENT`、`LOG_LEVEL`、`DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`、`DEPENDENCY_TIMEOUT_SECONDS`。连接池限制按进程计算，多 Worker 部署时需结合数据库连接总额调整。

## 模型网关

已预留两个可配置的调用档位：`fast` 使用 `qwen3.8-flash`，`reasoning` 使用 `qwen4-max`。档位是业务用途配置，不代表已完成模型质量评测。

将 API Key 写入本地 `.env` 的 `NEXUSGUARD_LLM_API_KEY`。`.env` 已被 Git 和 Docker 构建上下文忽略，建议权限为 `600`；示例文件不含真实 Key。

```bash
python -m nexusguard.tools.smoke_llm
```

该命令对每个模型各发送一次简短测试请求，可能产生少量费用；不发送订单数据，不打印凭据、请求正文或模型输出，仅报告状态、耗时和 Token 用量。测试不会在服务启动、健康检查或 CI 中自动运行。

网关提供异步连接复用、每进程并发上限、包含排队时间的超时、输出 Token 上限与响应校验。默认不重试、不自动切换模型；错误仅返回稳定错误码和 HTTP 状态，不透传上游响应。当前不支持模型工具调用，也不赋予模型业务写权限。

配置项均使用 `NEXUSGUARD_` 前缀：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_FAST_MODEL`、`LLM_REASONING_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_MAX_CONCURRENCY`、`LLM_MAX_OUTPUT_TOKENS`。未配置 Key 时 API 仍可启动；显式调用网关时返回未配置错误。

Compose 默认只启动基础设施骨架；如需容器内使用模型，应在部署配置中通过环境变量或密钥管理注入模型配置，避免构建进镜像。

## 检查

```bash
ruff check .
ruff format --check .
pytest
```

测试不依赖真实 PostgreSQL/Redis；就绪检查使用模拟探针验证成功、失败与超时路径。真实数据库迁移、并发控制和支付对账需要后续集成测试。

## 数据库迁移

禁止应用启动时自动 `create_all`。当前仅提供迁移入口，尚无业务表。

业务 ORM 模型完成后，在 `migrations/env.py` 导入模型，再执行并人工审核迁移：

```bash
alembic revision --autogenerate -m "create business tables"
alembic upgrade head
```

## 下一步

优先落地订单、售后单、工作流、审批、资金流水和 Outbox 的数据库模型与约束，再实现规则引擎及持久化挂起/恢复。退款模式、退货入库条件、退货率统计口径、生鲜特批规则仍需确定。

已提供独立模型网关，尚未实现业务 Agent；支付、身份认证或真正的审批执行仍未接入，不具备生产业务处理能力。
