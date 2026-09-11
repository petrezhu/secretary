# Secretary — Personal Digital Assistant System

[中文](README.zh-CN.md) | English

A standalone daemon running 24/7, monitoring server health, goal progress, knowledge base, and wealth engine — proactively communicating with the user via QQ and Email.

## System Positioning

Secretary is a **proactive** personal assistant that doesn't wait for questions, but continuously monitors indicators and pushes notifications at the right time:

- 🖥️ **Server Monitoring** — Memory/CPU/disk threshold alerts, service liveness checks, auto-repair
- 🎯 **Goal Coaching** — Overdue goal detection, energy state assessment, daily focus recommendation, adaptive plan adjustment
- 💰 **Wealth Engine** — A-share portfolio snapshots, sector analysis, national team signals, QDII monitoring, risk control decisions
- 📬 **Notification Dispatch** — QQ Bot + Email dual channels, tiered by severity (INFO/WARNING/CRITICAL)
- ☀️ **Morning Briefing** — Daily at 08:00, includes today's priorities, pending tasks, stuck goals, and yesterday's completions
- 🤖 **Hermes Integration** — Intercepts QQ messages via the Gateway plugin; simple intents get direct replies, complex queries go to the Agent

## Architecture Overview

![Architecture](docs/assets/architecture.svg)

## Module Details

### monitor/ — Monitor Layer
| Module | Function |
|--------|----------|
| `health.py` | System health check (memory/CPU/disk/service status) |
| `deadman.py` | Dead man's switch — detects whether the target system is still running |
| `resource_guard.py` | Resource guard — exponential backoff on threshold breach (600s→1200s→…→9600s) |

### engine/ — Engine Layer
| Module | Function |
|--------|----------|
| `scheduler.py` | Task scheduler, 30s tick interval, manages Job queue |
| `repairer.py` | Auto-repairer — service_down/docker_down/disk_full/cron_stuck |
| `audit.py` | Audit log, records all operations to audit.jsonl |

### coach/ — Coach Layer
| Module | Function |
|--------|----------|
| `energy.py` | User energy state assessment (high/medium/low) |
| `focus.py` | Today's focus selection algorithm |
| `overdue.py` | Overdue goal detection and severity assessment |
| `perception.py` | Perception engine — emotion and urgency inference |
| `planner.py` | Adaptive plan adjustment (PlanAdjustment) |
| `morning.py` | Morning briefing generator, selects sign-off based on energy state |

### wealth/ — Wealth Engine
| Module | Function |
|--------|----------|
| `portfolio.py` | Portfolio snapshot (PortfolioSnapshot) — total market value, P&L, notable changes |
| `sector_analysis.py` | Sector analysis — 8 sectors + 52-week + beta coefficient |
| `national_team.py` | National team fund signals (northbound/southbound/ETF inflows) |
| `eastmoney_sync.py` | EastMoney CDP data sync (passive keep-alive, 2h) |
| `decision.py` | Investment decision engine |
| `risk.py` | Risk control module |
| `qdii.py` | QDII fund monitoring |
| `redemption.py` | Redemption analysis |
| `monitor.py` | Individual stock keyword monitoring |
| `jobs.py` | Wealth engine scheduled task collection |

### gateway/ — User Interaction Entry Point
| Module | Function |
|--------|----------|
| `server.py` | FastAPI gateway service, port 8901 |
| `api.py` | API route definitions |
| `inbound.py` | `/api/inbound` intent router — direct reply for simple intents, pass to Agent for complex queries |
| `adapters.py` | Adapter protocol (GatewayAdapter) and registry (AdapterRegistry) |

### harness/ — Agent Execution Engine
| Module | Function |
|--------|----------|
| `base.py` | BaseHarness base class — connection/timeout/error handling |
| `hermes.py` | HermesHarness — integrates with Hermes Agent (CLI Proxy API) |
| `registry.py` | Harness registry, selects engine by name |

## Hermes Integration

Secretary works with the Agent through the Hermes plugin mechanism:

### gateway-interceptor Plugin (Separate repo: [gateway-interceptor](https://github.com/petrezhu/secretary-gateway))

> Universal message interception layer, supports any Agent Harness (Hermes/OpenClaw/QClaw/MimoClaw).
> Plugin source is a standalone open-source project.
> See [`plugins/gateway-interceptor`](https://github.com/petrezhu/secretary-gateway)

Registers a `pre_gateway_dispatch` hook to intercept messages, enhance via ASR/OCR, then route:

```
User message → [gateway-interceptor plugin]
                │
                ├─ ASR (speech→text) / OCR (image→text)
                ├─ POST /api/inbound → Secretary
                │     ├─ action: "handle" → direct reply, skip Agent
                │     └─ action: "allow"  → pass through to Agent
                │
                └─ Secretary unreachable → fail-open, go to Agent
```

**Configuration (environment variables):**
| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_DAEMON_URL` | `http://127.0.0.1:8901` | Gateway daemon address |
| `GATEWAY_DAEMON_TIMEOUT` | `3` | API call timeout (seconds) |
| `GATEWAY_INTERCEPT_PLATFORMS` | `qqbot` | Intercepted platforms (comma-separated, empty=all) |
| `ASR_MODELS` | `MiMo-V2.5-ASR` | ASR model fallback chain |
| `OCR_MODELS` | `deepseek-v4-flash,deepseek-v4-pro` | OCR model fallback chain |

## Supported Intents (/api/inbound)

Intent definitions live in `src/secretary/gateway/intents/`, pure regex matching, no LLM dependency.
Messages sent to the QQ Bot matching the following triggers are replied to directly by Secretary (bypassing the Agent):

![Message Flow](docs/assets/message-flow.svg)

| Domain | Trigger Phrases | Reply Content |
|--------|-----------------|---------------|
| Help | `有什么指令` / `有什么命令` / `/help` / `帮助` / `你能做什么` | Capability command list |
| Social | `你好` / `谢谢` / `厉害` etc. standalone words | Time-of-day greeting / courtesy |
| Task | `待办` / `有什么任务` | Active tasks + weekly goal summary |
| Task | `记一下任务：xxx` / `帮我创建任务 xxx` | Write to tasks.db and return ticket number |
| Task | `完成#142` / `搞定任务142` | Mark complete and return ✅ |
| Task | `任务#142` / `查一下#142` | Task details |
| Goal | `今天做什么` / `今日焦点` / `先做哪个` | Focus goal + next step suggestion |
| Goal | `长期目标` / `年度目标` | Goals grouped by domain with progress |
| Goal | `收件箱` / `未处理消息` | Unprocessed inbox entries |
| Wealth | `查一下持仓` / `市值多少` / `盈亏怎么样` | Portfolio overview (read-only) |
| Wealth | `大盘` / `行情` / `沪深300` | Real-time index movements (Tencent API) |
| Wealth | `qdii` / `溢价` / `套利` | Redirects to Agent for full analysis |
| System | `服务器状态` / `内存` / `磁盘` | CPU/memory/disk usage |
| System | `清理内存` / `清理进程` / `孤儿进程` | Auto-detect and clean orphan/duplicate/leftover processes |
| System | `上次存档` / `checkpoint` | Last archive date and days elapsed |
| System | `早报` / `日报` / `今天安排` | Re-send today's morning briefing |
| Emotion | 累了/烦躁 etc. emotional phrases | Soothing reply (perception engine) |

**Quiet hours (23:00–08:00):** Unrecognized ordinary messages won't disturb the Agent;
replies with "Not bothering with this now, will look again tomorrow morning." Other messages still fail-open to the Agent as usual.

**Design principle:** Only answer intents backed by real data; when data is missing or external APIs fail, always pass through to the Agent. All wealth intents are read-only — no trade execution.

## Configuration

Secretary uses environment variables for all personal configuration. First-time setup:

```bash
cp .env.example .env
# Edit .env with your actual values
```

Key environment variables:

| Variable | Description | Required |
|----------|-------------|----------|
| `SECRETARY_DATA_DIR` | Hermes workspace data directory | ✅ |
| `SECRETARY_USER_NAME` | What Secretary calls you | No (default "主人") |
| `SECRETARY_PORTFOLIO_PATH` | Portfolio data file path | Required for wealth features |
| `SMTP_USER` / `SMTP_PASSWORD` | Email notification config | Required for email |
| `QQ_APP_ID` / `QQ_CLIENT_SECRET` | QQ Bot config | Required for QQ |

### Local Configuration (not in git)

Besides `.env`, Secretary automatically merges two optional local config files at runtime
(effective if present, silently skipped if missing or malformed — does not affect startup):

| File | Purpose |
|------|---------|
| `config/local.json` | Personal self-hosted system list (merged into system list intent) |
| `config/local_stocks.json` | Personal stock/fund name patterns (entity recognition + sector keywords) |

Both are excluded in `.gitignore` and will never enter version history. Format:

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
  "stock_patterns": [{"pattern": "(StockA|Ticker1)"}],
  "sector_overrides": {"Tech Growth": {"extra_keywords": ["StockA"]}}
}
```

## Quick Start

```bash
cd secretary

# Install (development mode)
pip install -e ".[dev]"

# Run checks
secretary check

# Start daemon
secretary start

# Stop daemon
secretary stop
```

### systemd Deployment

```bash
sudo cp scripts/secretary.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now secretary

# View logs
journalctl -u secretary -f
```

### Runtime Thresholds

| Resource | Default Threshold | Description |
|----------|-------------------|-------------|
| Memory | 95% | memory_percent |
| CPU | 95% | cpu_percent (5min average) |
| Disk | 95% | disk_percent |

### Notification Rules

| Level | Channel | Behavior |
|-------|---------|----------|
| CRITICAL | QQ | Immediate push |
| WARNING | QQ | Batched push, max 5/hour |
| INFO | QQ | Scheduled push, daily at 08:00 |

## Project Structure

```
secretary/
├── config/                    # Configuration files
│   ├── default.yaml          # Main config
│   └── morning.yaml          # Morning briefing template
├── docs/                      # Documentation
│   ├── SPEC.md               # System specification
│   ├── CONTEXT.md            # Glossary
│   └── adr/                  # Architecture decision records
├── plugins/                   # Hermes plugins
│   └── gateway-interceptor/  # Hermes plugin
├── scripts/                   # Ops scripts
│   ├── secretary.service     # systemd unit file
│   ├── install.sh            # Install script
│   └── generate_allocation.py # Asset allocation generator
├── src/secretary/             # Source code
│   ├── __main__.py           # CLI entry point
│   ├── config.py             # Config loader
│   ├── daemon.py             # Main daemon lifecycle
│   ├── monitor/              # Monitor layer
│   ├── engine/               # Engine layer
│   ├── coach/                # Coach layer
│   ├── wealth/               # Wealth engine
│   ├── gateway/              # Gateway layer
│   ├── harness/              # Agent engine abstraction
│   ├── data/                 # Data layer
│   ├── notify/               # Notification layer
│   └── api/                  # API layer
├── tests/                     # Tests
│   ├── unit/                 # Unit tests (20+ files)
│   ├── integration/          # Integration tests
│   └── e2e/                  # End-to-end tests
└── pyproject.toml             # Project metadata
```

## Tech Stack

- **Language**: Python ≥3.9 (hatchling build)
- **Web**: FastAPI + Uvicorn
- **Monitoring**: psutil
- **Configuration**: PyYAML
- **Testing**: pytest + pytest-asyncio + pytest-cov
- **Linting**: ruff

## Documentation

- [SPEC.md](docs/SPEC.md) — System specification (thresholds/scheduling/repair/notifications)
- [CONTEXT.md](docs/CONTEXT.md) — Glossary (data structure definitions for each module)
- [ADR](docs/adr/) — Architecture decision records
