# 核心数据模型与数据库约束

数据基础阶段交付 SQLAlchemy 模型、独立初始迁移及真实 PostgreSQL 集成测试；客服接待另有增量迁移。模型统一注册于 `src/nexusguard/infrastructure/models/__init__.py`，数据库结构由 `0001_core` 迁移创建。

## 通用约定

- 租户业务表的主键为 `(tenant_id UUID, id UUID)`，查询和关联必须同时携带租户。`Tenant` 是租户根表，`InboxEvent` 使用消费去重组合主键。
- `created_at`、`updated_at` 等时间使用 `TIMESTAMPTZ`；开发库默认时区为 UTC。
- 金额为 `BIGINT`，单位为分，禁止浮点数。币种为三位大写代码，订单默认为 CNY；订单明细继承订单币种。
- 业务快照、检查点和事件载荷使用 JSONB。它们不能被视为已认证的指令，也不应包含 API Key 等凭据。
- 可变记录含 `version INTEGER`，SQLAlchemy ORM 使用乐观锁。Core SQL/原生 SQL 必须显式递增版本，并使用版本条件更新或行锁。
- 外键不级联删除业务、资金和审计历史。

## 身份、商品与订单

| 表 / 模型 | 业务字段 | 关键约束 |
| --- | --- | --- |
| `tenants` / Tenant | `id: UUID`, `name: VARCHAR(120)` | 租户根记录 |
| `users` / User | `external_id: VARCHAR(128)`, `status: active/suspended` | 租户内外部用户标识唯一 |
| `user_risk_profiles` / UserRiskProfile | `user_id: UUID`, `return_count: INTEGER`, `eligible_order_count: INTEGER`, `window_start/window_end/calculated_at: TIMESTAMPTZ`, `calculation_version: VARCHAR(64)` | 退货数不大于统计订单数，统计窗口有效；追加写 |
| `skus` / SKU | `sku_code`, `product_id`, `title`, `category`, `specifications: JSONB`, `price_cents: BIGINT`, `currency`, `sale_status`, `shipping_sla_id` | 租户内 SKU 编码唯一，价格非负 |
| `inventory` / Inventory | `sku_id: UUID`, `warehouse_id`, `on_hand/reserved: INTEGER` | 每个 SKU/仓库一条库存记录，`0 <= reserved <= on_hand` |
| `orders` / Order | `order_number`, `user_id: UUID`, `payment_status`, `fulfillment_status`, `paid_amount_cents: BIGINT`, `currency`, `ordered_at/paid_at/delivered_at`, `shipping_address_snapshot: JSONB` | 租户内订单号唯一，用户归属有效，实付非负 |
| `order_items` / OrderItem | `order_id/sku_id: UUID`, `quantity: INTEGER`, `paid_amount_cents: BIGINT`, `category_snapshot`, `sku_snapshot: JSONB`, `policy_reference` | 数量为正，商品与订单在同一租户 |
| `shipments` / Shipment | `order_id: UUID`, `carrier`, `tracking_no`, `status`, `promised_delivery_at/shipped_at/delivered_at` | 支持一个订单多个包裹 |
| `logistics_events` / LogisticsEvent | `shipment_id: UUID`, `source`, `source_event_id`, `event_type`, `occurred_at/received_at`, `payload: JSONB` | 按租户、来源和事件号去重；轨迹时间与接收时间分开 |

支付状态记录支付生命周期，退款金额与预占余额在退款账本中维护。顾客改址与仓库开始出库共享订单行锁。只有本人订单处于 `unshipped` 且未关闭时可以改址，开始出库后状态为 `processing`。改址直接更新 `shipping_address_snapshot`，并递增订单版本。地址字段为 `recipient`、`phone`、`region` 和 `street`。

退货率不存储为浮点数，由可信统计中的分子、分母计算。分母为零、统计缺失或过期时不能直接判低风险。

## 售后与裁决

| 表 / 模型 | 业务字段 | 关键约束 |
| --- | --- | --- |
| `aftersales_cases` / AfterSalesCase | `case_number`, `order_id/user_id: UUID`, `claim_type`, `requested_amount_cents`, `approved_amount_cents`, `currency`, `reason`, `status` | 订单、用户、币种必须匹配；已批准金额不得高于申请金额 |
| `case_items` / CaseItem | `case_id/order_id/order_item_id: UUID`, `quantity`, `requested_amount_cents` | 售后单与明细必须属于同一个订单；同一售后单的商品明细不重复 |
| `evidence` / Evidence | `case_id: UUID`, `object_key`, `content_hash`, `verification_status`, `verification_source`, `verified_at`, `verification_reason` | SHA-256 格式；非待核验状态需有来源、时间和依据 |
| `policy_decisions` / PolicyDecision | `case_id`, `currency`, `decision_version`, `policy_version`, `fact_snapshot`, `facts_hash`, `matched_rule_ids`, `eligibility`, `max_refund_cents`, `proposed_refund_cents`, `reason_codes` | 版本唯一；非合规裁决不能携带拟退款金额；拟退款额不超过规则上限；追加写 |
| `risk_assessments` / RiskAssessment | `case_id/decision_id`, `risk_rule_version`, `risk_level`, `requires_approval`, `triggered_rules`, `profile_snapshot` | 每个裁决一份风险快照；高风险必须要求审批；追加写 |

`eligibility` 为 `eligible / ineligible / needs_information`。生鲜政策、七天期限、有效凭证核验、退货入库条件、统计窗口和累计金额风控尚未实现，不能由这些字段的存在推断业务已经合规。

## 工作流与审批

| 表 / 模型 | 业务字段 | 关键约束 |
| --- | --- | --- |
| `workflow_instances` / WorkflowInstance | `case_id`, `state`, `checkpoint: JSONB`, `next_run_at`, `last_error_code`, `version` | 每个售后单一个工作流；状态值受约束，检查点必须是 JSON 对象 |
| `approval_requests` / ApprovalRequest | `workflow_id/case_id/decision_id/risk_assessment_id`, `approval_round`, `action_type`, `eligibility`, `amount_cents`, `currency`, `scope_hash`, `status`, `expires_at`, `reviewer_id`, `review_comment`, `decided_at` | 同一工作流审批轮次唯一；绑定合规裁决、金额和币种；人工结论需有审核主体、时间和备注 |

审批 `reviewer_id` 是后续认证系统中的主管主体标识，不是用户聊天输入。一次审批只绑定一个裁决和一轮审批，修改金额或重算裁决需要新审批。当前审批类型仅支持 `refund`；政策例外审批、发券审批未来单独设计。

工作流状态枚举与领域状态图一致。数据库当前仅校验合法状态值和版本，不自动运行状态机，也不替代接口的鉴权与状态流转授权。

## 资金与事件

| 表 / 模型 | 业务字段 | 关键约束 |
| --- | --- | --- |
| `order_refund_balances` / OrderRefundBalance | `order_id`, `currency`, `paid_cents`, `refunded_cents`, `reserved_cents`, `version` | 每单一个账本；币种及实付与订单一致；`0 <= 已退 + 预占 <= 实付` |
| `financial_actions` / FinancialAction | `order_id/case_id/decision_id/risk_assessment_id/approval_id`, `eligibility`, `approval_status`, `action_type`, `idempotency_key`, `request_hash`, `amount_cents`, `currency`, `requires_approval`, `status`, `provider`, `provider_request_id`, `provider_transaction_id`, `last_error_code` | 租户内幂等键唯一；支付方请求号、交易号去重；金额与合规裁决一致；风险标记不可与风险快照矛盾；高风险必须关联已同意的审批 |
| `audit_logs` / AuditLog | `actor_type/actor_id`, `action`, `resource_type/resource_id`, `from_state/to_state`, `reason_codes`, `details`, `trace_id` | 数据库触发器拒绝 UPDATE/DELETE |
| `outbox_events` / OutboxEvent | `aggregate_type/aggregate_id`, `event_type`, `payload`, `available_at`, `published_at`, `retry_count` | 未发布任务的部分索引；支持与业务变更同事务提交 |
| `inbox_events` / InboxEvent | `tenant_id`, `consumer`, `event_id`, `processed_at` | `(tenant_id, consumer, event_id)` 唯一，消费者分别去重 |

资金执行状态为 `pending / executing / succeeded / reconciling / failed`。记录成功必须具有支付方交易号。首版资金动作只允许 `refund`；发券工具未来增加独立政策和迁移。

## 数据库保证与应用职责

这些约束能阻止重复资金动作建单、错误关联、越额余额写入和部分审批绕过。但资金流水与退款账本仍须由后续执行服务在同一事务中创建和预占，不能将单独插入一条资金流水视为完成退款。

执行服务还必须实现身份认证、订单查询过滤、审批人权限、审批有效期、业务合规核验、执行前重新核验、幂等键请求参数比较、支付方调用与对账。复合外键能阻止跨租户关联，不能替代查询时的租户过滤或数据库 RLS。

追加写触发器约束普通行 UPDATE/DELETE；数据库所有者或超级用户仍有管理权限，不能据此宣称防篡改存档。生产运行角色需要最小权限和独立迁移账号。

检查点、审批单、审计和 Outbox 同事务提交及回滚，已通过真实数据库测试；售后审批 Worker、断点恢复接口和外部事件投递尚未实现；客服只读 Worker 单独实现。

## 客服接待增量模型

`0002_reception` 新增两张表，不修改首版迁移历史：

| 表 | 字段与用途 |
| --- | --- |
| `reception_sessions` | `tenant_id/id` 复合主键；`user_id`、`entry_point`、`state`、`candidate_order_id`、`bound_order_id`、`sku_id`、时间戳与乐观锁 `version` |
| `reception_messages` | `tenant_id/id` 复合主键，ID 由客户端生成用于去重；`session_id`、`text`、可空 `order_id`、`status`、`created_at` |

会话状态与候选/绑定字段有一致性约束，两个订单引用都绑定当前用户；订单表新增
`(tenant_id, id, user_id)` 唯一约束供外键引用。每会话最多一条 `needs_order` 消息，
关联订单由服务核验；消息状态更新通过锁定所属会话串行化。文本可能包含个人信息，
生产部署需补充保留期限、删除及访问策略。流程见 [客服接待流程](flows/customer-reception.md)。

## 迁移与验证

```bash
alembic upgrade head
alembic check
```

初始迁移包含 21 张业务/运营表和 4 张历史表的追加写触发器，不依赖导入当前版本模型来执行迁移。`alembic downgrade base` 会删除这些表及数据，只用于显式的测试环境或经审核的回滚。

集成测试要求显式设置 `NEXUSGUARD_TEST_DATABASE_URL`，数据库名以 `nexusguard_test_` 开头且无业务表。测试会执行升级、模型差异检查、回滚、再次升级，并在结束后清理该测试库中的迁移表结构。

```bash
NEXUSGUARD_TEST_DATABASE_URL=postgresql+asyncpg://nexusguard:nexusguard_dev@localhost:5432/nexusguard_test_persistence pytest -m integration
```

普通单元测试运行 `pytest -m 'not integration'`。完整测试运行 `pytest` 并提供上述环境变量；未提供时数据库测试会明确跳过。CI 为集成测试启动独立 PostgreSQL 16 服务，不调用真实模型或支付接口。

## 客服 Agent 增量模型

`0003_agent` 扩展消息并新增回复表，不修改前两次迁移历史：

| 表 | 字段与用途 |
| --- | --- |
| `reception_messages` | 新增 `intent: JSONB?`（实际模型名和校验后的结构）、`context: JSONB`（提交时状态及订单/SKU引用）、`worker_token: UUID?`（领取令牌） |
| `reception_replies` | `tenant_id/id`、`message_id`、`phase`、`text`、`facts: JSONB`、`created_at`；同消息同阶段唯一 |

消息状态扩展为 `queued / processing / needs_order / ready_for_agent / completed / failed / superseded`。
回复阶段为 `clarification / answer / error`；同一问题选单后可以同时有澄清回复和最终回答。
Outbox 的 `available_at` 作任务下次可领取时间，`retry_count` 记录领取次数；令牌隔离过期消费者。
回复、状态、审计与 Inbox 确认同事务提交。内部事实快照仅供后台使用，历史接口不返回该快照或原始意图。
新增迁移未在本次开发中执行；包含新状态消息时回滚会拒绝删除，以免丢失工作。

## 多商品破损信息收集

`0004_damage` 新增三个模型，均以租户限定访问，不提前生成正式售后单或资金流水：

| 表 / 模型 | 字段与用途 |
| --- | --- |
| `damage_drafts` / DamageDraft | 会话、用户、订单、`focused_item_id`、`pending_claims: JSONB`（待用户关联的原话主张）、`status=collecting/ready_for_review`、时间与版本；同会话同订单唯一 |
| `damage_draft_items` / DamageDraftItem | 草稿、订单、订单明细、是否纳入申报、可空破损数量和用户原话描述、来源消息、时间与版本；同草稿同明细唯一 |
| `damage_attachments` / DamageAttachment | 客户端 UUID、草稿项、媒体类型、SHA-256、实际图片 `BYTEA`、接收时间、`verification_status=pending`；同商品同内容去重 |

明细与草稿订单使用复合外键关联，数量不能大于购买数量由服务验证。图片大小、格式种类、哈希格式和未审核状态有数据库约束，单草稿数量/总字节上限由服务在会话锁内验证。

### 破损材料审核

`0005_damage_review` 新增 `damage_reviews` 和 `damage_review_items`。前者保存草稿、订单号、版本、状态和顾客通知，同一草稿最多有一个待处理版本。后者逐项保存商品、数量、描述、照片引用、结论、原因、审核人及时间。照片移除使用 `damage_attachments.removed_at`，旧审核版本通过原照片 UUID 读取保留的文件。

`0006_damage_application` 在提交版本上增加顾客诉求与人工处理结论。人工结论字段包含处理结果、原因、说明、审核员、处理时间，以及可选的退款金额和币种。通知内容由 Worker 写入该版本，`notified_at` 记录生成时间和会话中的显示顺序。
列表不返回图片字节。详情、限制、事务与未实现部分见 [破损信息收集](damage-collection.md)。
