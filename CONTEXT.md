# Secretary 项目术语表

本文件定义 Secretary 项目的领域词汇。工程技能（to-spec、to-tickets、triage 等）输出命名时必须使用本表术语，不替换为同义词。

## 模块词汇表

### wealth — 财富引擎
- **PortfolioSnapshot**: 持仓快照，含总市值、盈亏、显著变动
- **NationalTeamResult**: 国家队资金信号（北向 / 南向 / ETF 流入）
- **MonitorResult**: 个股监控结果（润泽科技关键词扫描）
- **WealthJobs**: 财富引擎定时任务集合（持仓刷新、信号监控）

### gateway — 用户交互入口
- **GatewayAdapter**: 网关适配器协议（QQ Bot / Email）
- **AdapterRegistry**: 适配器注册表，按名称分发消息
- **IntentHandler**: 意图处理器协议（regex 或精确关键词 + handle）
- **IntentRegistry**: 意图注册表，按序分发消息意图
- **create_server**: 创建 FastAPI 网关服务实例

### harness — Agent 执行引擎抽象
- **AgentHarness**: 统一 Agent 接口协议（llm_call / execute_tool / get_context / health_check）
- **BaseHarness**: 基类，提供连接、超时、错误处理
- **HermesHarness**: Hermes Agent 的 Harness 实现
- **HarnessRegistry**: Harness 注册表，按名称选择引擎

### coach — 目标督导层
- **EnergyState**: 用户能量状态评估（high / medium / low）
- **FocusResult**: 今日焦点选择结果
- **OverdueGoal / Severity**: 逾期目标检测与严重度
- **PerceptionEngine / Emotion / Urgency**: 感知引擎，情绪与紧急度推断
- **AdaptivePlanner / PlanAdjustment**: 自适应计划调整器