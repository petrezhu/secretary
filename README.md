# Secretary — 个人数字助理系统

全天候运行的独立 daemon，监控服务器健康/目标进度/知识库/财富引擎，通过 QQ 和 Email 主动与用户沟通。

## 系统定位

Secretary 是一个**主动式**个人助理，不等用户提问，而是持续监控各项指标并在适当时机主动推送通知：

- 🖥️ **服务器监控** — 内存/CPU/磁盘阈值告警，服务存活检测，自动修复
- 🎯 **目标督导** — 逾期目标检测、能量状态评估、每日焦点推荐、自适应计划调整
- 💰 **财富引擎** — A股持仓快照、板块分析、国家队信号、QDII 监控、风控决策
- 📬 **通知分发** — QQ Bot + Email 双通道，按级别(INFO/WARNING/CRITICAL)分级推送
- ☀️ **早安简报** — 每日 8:00 自动生成，含今日重点、待办任务、卡住目标、昨日完成
- 🤖 **Hermes 集成** — 通过 Gateway 插件拦截 QQ 消息，简单意图直接回复，复杂查询交给 Agent

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Gateway 层 (gateway/)                 │
│         FastAPI 网关 · 用户交互入口 · 适配器注册表        │
├─────────────────────────────────────────────────────────┤
│                    Harness 层 (harness/)                 │
│         Agent 执行引擎抽象 · HermesHarness 实现          │
├────────────┬────────────┬───────────────┬───────────────┤
│  监控层     │  引擎层     │  督导层        │  财富引擎     │
│  monitor/  │  engine/   │  coach/        │  wealth/      │
│            │            │               │               │
│ · 健康检查  │ · 调度器    │ · 能量评估     │ · 持仓快照    │
│ · 死人开关  │ · 自动修复  │ · 焦点选择     │ · 板块分析    │
│ · 资源守护  │ · 审计日志  │ · 逾期检测     │ · 国家队信号  │
│            │            │ · 感知引擎     │ · QDII/风控   │
│            │            │ · 自适应规划   │ · 东方财富同步 │
├────────────┴────────────┴───────────────┴───────────────┤
│                    数据层 (data/)                        │
│         Repository · goals.db · tasks.db · Obsidian      │
├─────────────────────────────────────────────────────────┤
│                    通知层 (notify/)                      │
│         Dispatcher · QQ Bot 适配器 · Email 适配器        │
└─────────────────────────────────────────────────────────┘
```

## 模块详解

### monitor/ — 监控层
| 模块 | 功能 |
|------|------|
| `health.py` | 系统健康检查（内存/CPU/磁盘/服务状态） |
| `deadman.py` | 死人开关 — 检测目标系统是否还在正常运行 |
| `resource_guard.py` | 资源守护 — 超阈值时指数退避（600s→1200s→…→9600s） |

### engine/ — 引擎层
| 模块 | 功能 |
|------|------|
| `scheduler.py` | 任务调度器，30s tick 间隔，管理 Job 队列 |
| `repairer.py` | 自动修复器 — service_down/docker_down/disk_full/cron_stuck |
| `audit.py` | 审计日志，记录所有操作到 audit.jsonl |

### coach/ — 督导层
| 模块 | 功能 |
|------|------|
| `energy.py` | 用户能量状态评估 (high/medium/low) |
| `focus.py` | 今日焦点选择算法 |
| `overdue.py` | 逾期目标检测与严重度评估 (Severity) |
| `perception.py` | 感知引擎 — 情绪与紧急度推断 (Emotion/Urgency) |
| `planner.py` | 自适应计划调整器 (PlanAdjustment) |
| `morning.py` | 早安简报生成器，结合能量状态选择结尾语 |

### wealth/ — 财富引擎
| 模块 | 功能 |
|------|------|
| `portfolio.py` | 持仓快照 (PortfolioSnapshot) — 总市值/盈亏/显著变动 |
| `sector_analysis.py` | 板块分析 — 8板块+52周+β系数 |
| `national_team.py` | 国家队资金信号（北向/南向/ETF 流入） |
| `eastmoney_sync.py` | 东方财富 CDP 数据同步（被动保活 2h） |
| `decision.py` | 投资决策引擎 |
| `risk.py` | 风险控制模块 |
| `qdii.py` | QDII 基金监控 |
| `redemption.py` | 赎回分析 |
| `monitor.py` | 个股关键词监控 |
| `jobs.py` | 财富引擎定时任务集合 |

### gateway/ — 用户交互入口
| 模块 | 功能 |
|------|------|
| `server.py` | FastAPI 网关服务，端口 8901 |
| `api.py` | API 路由定义 |
| `inbound.py` | `/api/inbound` 意图路由 — 简单意图直接回复，复杂查询放行 Agent |
| `adapters.py` | 适配器协议 (GatewayAdapter) 与注册表 (AdapterRegistry) |

### harness/ — Agent 执行引擎
| 模块 | 功能 |
|------|------|
| `base.py` | BaseHarness 基类 — 连接/超时/错误处理 |
| `hermes.py` | HermesHarness — 对接 Hermes Agent (CLI Proxy API) |
| `registry.py` | Harness 注册表，按名称选择引擎 |

## Hermes 集成

Secretary 通过 Hermes 插件机制与 Agent 协同工作：

### gateway-interceptor 插件 (独立仓库: [gateway-interceptor](https://github.com/petrezhu/secretary-gateway))

> 通用消息拦截层，支持任意 Agent Harness (Hermes/OpenClaw/QClaw/MimoClaw)。
> 插件源码独立为开源项目，本仓库通过 symlink 引用。
> 见 [`plugins/gateway-interceptor`](https://github.com/petrezhu/secretary-gateway)

注册 `pre_gateway_dispatch` 钩子，拦截消息并经 ASR/OCR 增强后路由：

```
用户消息 → [gateway-interceptor 插件]
                │
                ├─ ASR (语音→文字) / OCR (图片→文字)
                ├─ POST /api/inbound → Secretary
                │     ├─ action: "handle" → 直接回复，跳过 Agent
                │     └─ action: "allow"  → 放行给 Agent 处理
                │
                └─ Secretary 不可达 → fail-open，走 Agent
```

**配置项（环境变量）：**
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GATEWAY_DAEMON_URL` | `http://127.0.0.1:8901` | Gateway daemon 地址 |
| `GATEWAY_DAEMON_TIMEOUT` | `3` | API 调用超时（秒） |
| `GATEWAY_INTERCEPT_PLATFORMS` | `qqbot` | 拦截的平台（逗号分隔，空=all） |
| `ASR_MODELS` | `MiMo-V2.5-ASR` | ASR 模型回退链 |
| `OCR_MODELS` | `deepseek-v4-flash,deepseek-v4-pro` | OCR 模型回退链 |

## 可自动处理的意图（/api/inbound）

意图定义在 `src/secretary/gateway/intents/`，纯正则匹配，无 LLM 依赖。
用户对 QQ Bot 发送以下消息会被 Secretary 直接回复（不经过 Agent）：

| 域 | 触发语 | 回复内容 |
|----|--------|----------|
| 帮助 | `有什么指令` / `有什么命令` / `/help` / `帮助` / `你能做什么` | 能力指令表 |
| 社交 | `你好` / `谢谢` / `厉害` 等独立词 | 时段问候 / 客套 |
| 任务 | `待办` / `有什么任务` | 活跃任务+周目标摘要 |
| 任务 | `记一下任务：xxx` / `帮我创建任务 xxx` | 写入 tasks.db 并回执编号 |
| 任务 | `完成#142` / `搞定任务142` | 标记完成并回执 ✅ |
| 任务 | `任务#142` / `查一下#142` | 任务详情 |
| 目标 | `今天做什么` / `今日焦点` / `先做哪个` | 焦点目标+推进建议 |
| 目标 | `长期目标` / `年度目标` | 按领域分组的目标进度 |
| 目标 | `收件箱` / `未处理消息` | inbox 未处理条目 |
| 财富 | `查一下持仓` / `市值多少` / `盈亏怎么样` | 持仓概览（只读） |
| 财富 | `大盘` / `行情` / `沪深300` | 指数实时涨跌（腾讯接口） |
| 财富 | `qdii` / `溢价` / `套利` | 引导至 Agent 完整分析 |
| 系统 | `服务器状态` / `内存` / `磁盘` | CPU/内存/磁盘使用率 |
| 系统 | `清理内存` / `清理进程` / `孤儿进程` | 自动检测并清理孤儿/重复/遗留进程 |
| 系统 | `上次存档` / `checkpoint` | 最后存档日期与天数 |
| 系统 | `早报` / `日报` / `今天安排` | 重发当日早安简报 |
| 情绪 | 累了/烦躁等情绪语 | 安抚回复（感知引擎） |

**免打扰时段（23:00–08:00）**：未识别的普通消息不惊扰 Agent，
回复「这个点我先不折腾了，明早再看」。其余消息照常 fail-open 给 Agent。

**设计原则**：只回答有真实数据支撑的意图；数据缺失或外部 API 失败一律
放行 Agent。财富类意图全部只读，不做任何交易写操作。

## 配置

Secretary 通过环境变量配置所有个人信息。首次使用：

```bash
cp .env.example .env
# 编辑 .env，填入你的实际配置
```

关键环境变量：

| 变量 | 说明 | 必填 |
|------|------|------|
| `SECRETARY_DATA_DIR` | Hermes workspace 数据目录 | ✅ |
| `SECRETARY_USER_NAME` | Secretary 对你的称呼 | 否（默认"主人"） |
| `SECRETARY_PORTFOLIO_PATH` | 持仓数据文件路径 | 财富功能需要 |
| `SMTP_USER` / `SMTP_PASSWORD` | 邮件通知配置 | 邮件功能需要 |
| `QQ_APP_ID` / `QQ_CLIENT_SECRET` | QQ Bot 配置 | QQ 功能需要 |

### 本地配置（不入 git）

除 `.env` 外，Secretary 在运行时会自动合并两个可选的本地配置文件
（存在即生效，缺失或格式损坏则静默跳过，不影响启动）：

| 文件 | 用途 |
|------|------|
| `config/local.json` | 个人自持系统清单（系统列表意图会合并展示） |
| `config/local_stocks.json` | 个人持仓股票/基金名称（记忆实体识别 + 板块关键词） |

两者均已在 `.gitignore` 中排除，永远不会进入版本历史。格式：

```jsonc
// config/local.json
{
  "systems": [
    {"name": "MyService", "description": "...", "location": "/opt/...",
     "commands": {"status": "..."}, "port": 8080, "notes": "..."}
  ]
}

// config/local_stocks.json
{
  "stock_patterns": [{"pattern": "(股票A|甲股)"}, {"pattern": "(基金X)"}],
  "sector_overrides": {"科技成长": {"extra_keywords": ["股票A"]}}
}
```

## 快速开始

```bash
cd secretary

# 安装（开发模式）
pip install -e ".[dev]"

# 运行检查
secretary check

# 启动 daemon
secretary start

# 停止 daemon
secretary stop
```

### systemd 部署

```bash
sudo cp scripts/secretary.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now secretary

# 查看日志
journalctl -u secretary -f
```

### 运行配置

| 资源 | 默认阈值 | 说明 |
|------|----------|------|
| 内存 | 95% | memory_percent |
| CPU | 95% | cpu_percent（5min 均值） |
| 磁盘 | 95% | disk_percent |

### 通知规则

| 级别 | 通道 | 行为 |
|------|------|------|
| CRITICAL | QQ | 立即推送 |
| WARNING | QQ | 批量推送，最多 5条/小时 |
| INFO | QQ | 定时推送，每日 08:00 |

## 项目结构

```
secretary/
├── config/                    # 配置文件
│   ├── default.yaml          # 主配置
│   └── morning.yaml          # 早安简报模板
├── docs/                      # 文档
│   ├── SPEC.md               # 系统规格说明
│   ├── CONTEXT.md            # 术语表
│   └── adr/                  # 架构决策记录
├── plugins/                   # Hermes 插件
│   └── gateway-interceptor/  # → symlink to /root/git/secretary-gateway (独立开源)
├── scripts/                   # 运维脚本
│   ├── secretary.service     # systemd 单元文件
│   ├── install.sh            # 安装脚本
│   ├── em_keepalive.py       # 东方财富保活
│   ├── em_cdp_watcher.py     # 东方财富 CDP 监控
│   ├── em_login_helper.py    # 东方财富登录辅助
│   └── generate_allocation.py # 资产配置生成
├── src/secretary/             # 源代码
│   ├── __main__.py           # CLI 入口
│   ├── config.py             # 配置加载
│   ├── daemon.py             # 主 daemon 生命周期
│   ├── monitor/              # 监控层
│   ├── engine/               # 引擎层
│   ├── coach/                # 督导层
│   ├── wealth/               # 财富引擎
│   ├── gateway/              # 网关层
│   ├── harness/              # Agent 引擎抽象
│   ├── data/                 # 数据层
│   ├── notify/               # 通知层
│   └── api/                  # API 层
├── tests/                     # 测试
│   ├── unit/                 # 单元测试（20+ 文件）
│   ├── integration/          # 集成测试
│   └── e2e/                  # 端到端测试
└── pyproject.toml             # 项目元数据
```

## 技术栈

- **语言**: Python ≥3.9 (hatchling 构建)
- **Web**: FastAPI + Uvicorn
- **监控**: psutil
- **配置**: PyYAML
- **测试**: pytest + pytest-asyncio + pytest-cov
- **Lint**: ruff

## 文档

- [SPEC.md](docs/SPEC.md) — 系统规格说明（阈值/调度/修复/通知）
- [CONTEXT.md](docs/CONTEXT.md) — 术语表（各模块数据结构定义）
- [ADR](docs/adr/) — 架构决策记录
