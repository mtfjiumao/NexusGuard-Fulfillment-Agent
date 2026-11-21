# Vue 前端：结构、运行与维护

前端采用 Vue 3 + TypeScript + Vite，后端继续使用 FastAPI。
Vue 负责页面与本地交互状态；订单推荐、归属校验、建议问题及业务权限仍由后端决定。
当前只有一个客服页面，因此不引入 Vue Router、Pinia 或组件库。

## 目录与职责

```text
frontend/
├── package.json / package-lock.json  # 命令与固定依赖版本
├── vite.config.ts                   # 开发代理和构建路径
└── src/
    ├── main.ts / App.vue            # Vue 启动与根组件
    ├── views/ReceptionView.vue      # 组合完整客服页面
    ├── components/
    │   ├── OrderCard.vue            # 订单信息展示
    │   ├── SuggestedQuestions.vue   # 建议问题按钮
    │   ├── OrderPicker.vue          # 分页选择历史订单
    │   ├── DamageDraftPanel.vue     # 多商品破损申报与按商品上传照片
    │   └── MessageComposer.vue      # 自由输入和发送
    ├── composables/useReception.ts  # 会话、请求、错误、重试状态
    ├── api/reception.ts             # HTTP 请求封装
    ├── api/types.ts                 # 与后端契约对应的类型
    └── style.css                    # 页面样式
```

可以从 `ReceptionView.vue` 开始读：它把会话状态交给子组件，通过事件接收用户操作。
子组件用 `props` 接收数据，用 `emit` 通知父组件；不会自行决定订单归属。
`useReception` 中的 `ref` 保存会变化的数据，`computed` 根据这些数据计算派生状态。
当会话变化时，Vue 自动更新订单卡片和问题按钮，不再逐一操作 DOM。

TypeScript 用于编写可检查的代码；它不会在浏览器中替代运行时校验，更不授予业务权限。
Vite 在开发时提供服务器和热更新，在部署前将 Vue/TypeScript 编译为浏览器能执行的文件。
业务接口变更时，同步维护 `api/types.ts` 与后端 `supervisor/schemas.py`。

## 开发启动

需要 Node.js 22.12+（22 系列）或 24+，以及 npm。推荐使用 Node.js 22。
后端数据库、Redis、迁移及认证接入方式见 README 与 [接待流程](flows/customer-reception.md)。

在仓库根目录启动后端：

```bash
source .venv/bin/activate
uvicorn nexusguard.main:create_app --factory --reload
```

另开终端：

```bash
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
npm run dev
```

打开 `http://127.0.0.1:5173/customer-service`。从订单页进入时加
`?entry_point=order&order_id=<UUID>`；店铺入口使用 `?entry_point=store`，可附加 `sku_id`。
Vite 将 `/api` 请求代理到 `http://127.0.0.1:8000`，浏览器始终请求当前源下的 API。
后端端口变更时调整 `vite.config.ts`；不需要为此开放通配 CORS。

现阶段尚未实现登录：未经可信认证中间件设置 `CustomerIdentity` 的 API 请求会返回 401，
页面提示先登录。接入现有登录系统时需同时配置开发代理与登录流程；不提供假身份或绕过鉴权。
另开终端执行 `python -m nexusguard.modules.supervisor.agent_worker`；不启动时消息将保持排队。
意图识别使用 `qwen3.8-flash`；`qwen4-max` 根据受限工具返回的事实和草稿缺项生成自然回复。

## 构建和部署

```bash
cd frontend
npm run typecheck
npm run build
```

`build` 自带一次类型检查，随后把生产文件生成到 `src/nexusguard/web/dist/`。
无需先单独运行 `typecheck` 再构建；前者用于只检查类型。
构建结果不提交到 Git；依赖变更时提交 `package.json` 和 `package-lock.json`，其他环境用 `npm ci` 安装。

本机部署时先构建，再启动或重启 FastAPI，访问 `http://127.0.0.1:8000/customer-service`。
页面由 FastAPI 返回，带哈希的 JS/CSS 位于 `/assets/reception/assets/`。
没有构建产物时，后端 API 仍能启动，页面入口明确返回 503 `frontend_build_required`。
不要使用 Vite 开发服务器作为生产服务器。

Dockerfile 已分为 Node 构建和 Python 运行两阶段，运行镜像不需要 Node；
执行 `docker compose build api` 会自动构建前端。手动制作 Python wheel 时，
需先构建前端，使 Hatch 将产物包含在 wheel 中。未构建的后端包仍可用于纯 API 开发。

## 交互与安全边界

- 选定订单后，卡片下方展示 2～3 个建议问题；切换订单后根据后端响应整组刷新。
- 点击建议问题与自由输入走同一消息接口。用户文字通过 Vue 文本插值显示，不使用 `v-html`。
- 请求进行时防止重复提交，但输入框仍可编辑；订单列表支持分页和失败重试。
- 消息结果不明确时保留原 UUID 并提供重试，确认前暂不切换订单或发送新消息。
  待确认请求只保存在当前页面内存中；刷新页面后的未确认请求恢复仍待后续设计。
- 状态版本冲突时获取最新会话，要求用户确认后重新操作，不自动把旧消息发送到新订单。
- 页面展示数据库中的用户消息、客服回复和处理状态，支持分页加载更早消息。
- 前端不持有模型 API Key、支付凭据或信任浏览器自报身份。不要将密钥写入 `VITE_*` 变量，
  这些变量可能进入浏览器构建产物。

遵守仓库约定：未经用户要求不新增或运行测试；构建和类型检查是前端交付检查。

## 后台进度与历史

`useReception` 每 2.5 秒读取会话视图和最近 50 条消息；请求串行调度，组件卸载后停止轮询。
后台处理期间暂停提交新问题或切换订单，输入框仍可编辑。同步失败时暂停写操作并重试，
避免把旧上下文当作新状态。历史接口支持 `before=<message_id>` 向前翻页。
创建会话后 URL 带 `session_id`，刷新时按该 ID 读取本人会话；该 ID 不授予访问权限。
消息发送结果不明的 UUID 仍只在当前页内存保存，刷新前应先重试确认。

## 破损申报面板

`ReceptionView.damage_draft` 存在时展示 `DamageDraftPanel`。每个订单商品独立显示破损数量、
原话描述和真实附件数量；选择/移除按钮是确定性操作，不调用模型。选择某商品后，可以直接用聊天补充。
照片以原始文件字节上传，服务端校验会话、草稿项和版本，再保存字节与哈希。
上传结果未确认时复用同一 UUID 重试，暂停其他写操作；文件仅保留在当前页面内存，刷新前应先确认。
材料齐全后，顾客可填写诉求并确认提交售后申请。审核员通过 `/damage-review` 核验材料并保存整份申请的处理结论，顾客在聊天记录中收到结果。演示审核员通过独立入口访问。详见 [破损售后申请](damage-review.md)。

## 本机模拟顾客

在本机 PostgreSQL 创建独立的 `nexusguard_demo` 数据库，并让开发配置中的数据库角色拥有该库。然后在项目根目录执行：

```bash
source .venv/bin/activate
python -m nexusguard.tools.demo_customer setup
python -m nexusguard.tools.demo_customer serve
```

打开 <http://127.0.0.1:8001/customer-service>。页面以固定模拟顾客身份显示示例订单 `DEMO-1001`。API 和 Agent Worker 一起启动，对话使用本地模型配置。审核工作台仍由宿主提供员工身份。

发送「我想买一点香蕉」，可查看在售商品卡片。点击卡片会在新标签打开本地商品详情。已运行过演示的环境可重新执行 `setup` 补齐示例香蕉 SKU。

演示香蕉照片缩放自 Evan-Amos 的 [Banana-Single.jpg](https://commons.wikimedia.org/wiki/File:Banana-Single.jpg)，按 [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) 使用。

## 本机自助改址演示

重新执行演示的 `setup` 会补齐已付款、尚未出库的订单 `DEMO-1002`。从客服选中该订单并发送「帮我改一下地址」，可核对并保存新地址。

要体验出库拦截，先保持改址表单打开，再从项目根目录运行 `python -m nexusguard.tools.demo_customer pack`。此命令将该演示订单推进到出库处理中。之后提交表单会收到出库拦截提示。出库状态和已保存的地址会随演示数据库保留。

## 本机人工查件演示

执行 `python -m nexusguard.tools.demo_customer setup` 后，选定异常订单 `DEMO-1003` 并发送「帮我查一下物流」。点击「请人工查件」，核对订单后确认提交。在审核员演示端打开 `/logistics-review`，查看异常包裹和记录，填写结果并办结。顾客发起查件的会话会收到通知。

宿主应用需为员工注入含 `logistics_review` 权限的 `request.state.reviewer_identity`。本地 `serve --port 8003 --reviewer` 已为演示审核员授予此权限。
