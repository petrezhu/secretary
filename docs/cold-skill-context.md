# ColdSkill 冷技能沉淀机制 — 设计上下文（to-spec 输入）

> 状态：设计已与用户确认（2026-09-10）。本文档是 `/to-spec` 的输入上下文。
> 目标规格：Secretary 的主动沉淀机制 + ColdSkill 能力单元系统。

---

## 1. 背景与目标

Secretary 是沉淀式智能体：规则引擎处理确定性业务，数据积累替代 LLM 推理，越用越精准、越用越省 token。

当前缺口：

1. **下沉留痕缺失**：`/api/inbound` 只在 `action=handle` 时记录 ledger；`action=allow`（流入 Agent）的消息零留痕。被 Agent 处理的高频问题全部蒸发，无法成为规则引擎的原料。
2. **规则只有硬编码**：关键词是手写 Python 集合，新增能力 = 改代码 + 同步插件三副本 + 重启。没有数据驱动的规则演进通道。
3. **沉淀无载体**：即便挖掘出高频问询，也没有严格的、可版本管理的、0 token 的能力单元格式去承接它。

目标：建立"问询留痕 → 候选挖掘 → 人工采纳 → 冷技能生效"的主动沉淀闭环；引入 ColdSkill（冷技能）契约，作为 0 token 能力单元的严格载体。

核心设计哲学（用户定调）：

- 冷智能以**脚本**为主；ColdSkill 是脚本的严格化形式，高于散脚本之处在于：自描述、可发现、可校验、可版本管理、被帮助菜单自动摄取。
- 冷技能与 Hermes skill 的本质区别：Hermes skill 是给 LLM 读的工作流知识（进 prompt、烧 token）；ColdSkill 是给规则引擎/调度器执行的确定性代码，**LLM 永不接触，0 token**。
- 分阶段：P1 数据层（留痕+挖掘），P2 技能层（ColdSkill 契约+采纳闭环）。

---

## 2. 核心概念

| 术语 | 定义 |
|---|---|
| 冷智能 | 规则引擎执行的确定性逻辑，0 token 消耗 |
| 热智能 | LLM/Agent 推理，消耗 token |
| ColdSkill（冷技能） | 自描述的确定性能力单元：manifest + 脚本（或内嵌文案），可被规则引擎路由、被帮助菜单渲染、被 git 版本管理 |
| 冷技能仓库 | 独立 git 仓库，存放用户级冷技能，Secretary 运行时热加载 |
| 候选（candidate） | 从问询日志挖出的高频未命中原文，待用户采纳 |
| 采纳（adopt） | 由候选生成（或挂靠）冷技能并立即生效 |

技能三模式：

| 模式 | 形态 | 触发 | 测试要求 |
|---|---|---|---|
| literal | 仅 manifest + 内嵌固定答复文案 | 关键词精确匹配 → 固定文案 | 无需测试 |
| attach | 仅 manifest，声明 `target` 意图 + 追加关键词别名 | 关键词精确匹配 → 已有核心意图 handler | 无需测试 |
| script | manifest + script.py，入口 `run(ctx) -> str | None` | 任意触发词 → 确定性逻辑 | 必须带 test_script.py |

采纳命令只产生 literal / attach 两种模式（机器模板化生成）；script 模式由人类开发者手写（含测试），用于以后"固化内置"或复杂冷逻辑。

---

## 3. 系统设计

### 3.1 问询全量留痕（P1）

- 位置：`/api/inbound` 是唯一咽喉，覆盖三条路径：>200 字符早退分支、三档 confidence（high/medium/low）。
- 每条消息记录：归一化原文、命中意图（或 `allow`）、时间戳，落 `queries.jsonl`（`$SECRETARY_DATA_DIR` 下）。
- 轮转策略与现有 handled ledger 一致（256KB 上限）。
- 既有 `secretary_handled.jsonl` 和 agent_context_bridge 不动。

### 3.2 候选挖掘（P1）

- 新增调度器 Job（挂现有 Scheduler，类比 morning_briefing），默认每周执行一次，纯规则聚合，零 LLM：
  - 同一原文出现 ≥ 阈值（默认 3 次/7 天）且每次都走 allow 路径 → 候选。
  - 候选使用**用户原话原文**（不归一化），天然满足精确全等匹配纪律，不踩子串误伤。
- 产出 `rule_candidates.json`（限期最近 N 天，默认 30 天），候选条目带：原文、出现次数、最近时间、建议动作类型。
- 阈值、周期、N 全部可配置。

### 3.3 冷技能仓库与加载器（P2）

- **独立 git 仓库**（决策 1）：用户级冷技能单独成库，不混入 Secretary 代码库。
  - 远端：Forgejo（主）+ GitHub（备份），与 secretary-gateway 双推惯例一致。
  - 运行时通过环境变量 `SECRETARY_COLD_SKILLS_DIR` 定位技能目录。
- **加载器**（ColdSkillLoader）：构建 registry 后扫描技能目录：
  - 依赖 manifest schema 静态校验（硬约束）；
  - script 模式做入口签名断言（`run(ctx)` 存在、返回 `str | None`）+ 测试文件存在性检查（硬约束）；
  - 技能目录加载失败/异常 → fail-open，绝不阻塞核心 intents；
  - 技能仓库不存在/被删 → 冷技能层静默禁用，核心意图不受影响；
  - 采纳后热生效（mtime 缓存 + 一次性失效），无需重启。
- **命名/规范契约**：manifest 使用 snake_case id、无空格、语义分组目录（可选）。

### 3.4 技能 manifest 契约

```yaml
id: qdii-cx-faq             # 唯一标识，snake_case
version: 3
description: "……"           # 一行文案，直接进帮助菜单
keywords: ["CXO是什么", "qdii成分股"]   # 精确全等匹配集合
mode: literal | attach | script
target: portfolio           # 仅 attach 模式：核心意图 handler 名
replies: ["固定答复……"]      # 仅 literal 模式
handler: "script.py"        # 仅 script 模式
entry: "run"
priority: 100               # 排序权重；核心 intents 恒高于所有技能
enabled: true
tags: ["财富", "faq"]       # 帮助菜单分组
llm_allowed: false          # 声明字段；不强制（见 4.4 软约束）
```

### 3.5 采纳/撤销（P2）

- 新增意图：
  - "建议"：展示规则候选清单（拉取式，不 push，尊重低打扰原则）。
  - "采纳建议#N"：把候选沉淀为技能目录 + git commit + push，立即生效。
    - `采纳建议#N 答复：xxx` → literal 模式；
    - `采纳建议#N 挂到 持仓` → attach 模式（把原话挂到已有意图）。
  - "技能列表"：展示冷技能仓库现状。
  - "撤销技能 X"：git rm + commit + push，立即失效。
- 采纳即生效 + 撤销兜底（决策 3）；失误可即时回滚。
- git push 失败：本地仍生效，回复用户"本地已生效，推送失败原因"，不阻塞命令返回。
- 新技能条目自动出现在帮助菜单"🧊 冷技能"分组：HelpHandler 已有 registry 驱动的动态渲染机制,loader 从 manifest description + keywords 提供分组数据,菜单零手写改动。

---

## 4. 已确认决策（用户拍板）

1. **冷技能仓库 = 独立 git 仓库版本管理**（不进 data dir、不混 secretary repo）。Forgejo 主 + GitHub 备份；运行时环境变量定位路径。
2. **核心 intents 一行不改**。冷技能层只承接新沉淀 + 后续逐步"固化内置"（script 模式 → code review → 合入 secretary repo 成为内置能力）。
3. **采纳即生效 + 撤销兜底**，不做"试运行"中间态。
4. **literal 模式不要求测试**；script 模式必须带测试。校验三件套：manifest schema 硬校验 / script 入口签名断言与测试存在检查（硬）/ literal 与 attach 免测试。
5. **LLM 调用只做软约束**：不做硬性禁用。采纳时对脚本做 AST 扫描，发现 LLM API 引用仅输出提示建议，不拒绝加载。manifest 保留 `llm_allowed` 声明字段（自述用，不强制）。

---

## 5. 数据流总览

```
用户消息 ──> /api/inbound ──> 核心 intents(精确匹配/正则)
                │  └─ handle → 回复（已有，不动）
                │  └─ allow  → Agent（原有通道）
                │
                └── 全量留痕 queries.jsonl（新增，P1）
                              │
                   每周聚合 Job（0 token，P1）
                              │
                    rule_candidates.json
                              │
              用户主动查询"建议"（拉取，低打扰）
                              │
         ┌──"采纳建议#N 答复/挂到"──┐
         │         （P2）          │
   literal技能目录  attach技能目录
         └──────────┬──────────┘
              （script 技能由人类编写，不走采纳命令）
              冷技能仓库 (git)
              git commit + push
                    │
           ColdSkillLoader 热加载
                    │
    规则引擎 registry 合并 + 帮助菜单渲染（0 token 全链路）
```

---

## 6. 测试与验收

- 既有 98+ 测试全量保持通过（回归基线）。
- Golden set 新增意图路由用例：建议 / 采纳建议#N / 撤销技能 / 技能列表。
- loader 单测：literal/attach 正确加载、非法 manifest 拒绝加载且不断核心链路、仓库缺失 fail-open、mtime 热生效。
- 留痕单测：>200 字早退、三档 confidence 全路径均落 queries.jsonl。
- 聚合 Job 单测：用构造的 queries.jsonl fixture 断言候选阈值/周期/去噪正确。
- 采纳语义必须保持精确全等匹配纪律（不因别名扩写成子串匹配）。

验收标准（行为级）：

1. 任意消息流入后 queries.jsonl 必有记录（含早退分支）。
2. 高频 allow 消息进入候选；阈值内消息不产生噪音。
3. 用户可用"建议"看到候选；"采纳"后立刻生效（不发重启）；"撤销"后立刻失效。
4. 采纳后新技能条目自动出现在帮助菜单"🧊 冷技能"分组。
5. 冷技能仓库离线时 Secretary 核心功能零影响。
6. 全程（留痕、挖掘、加载、路由、菜单渲染）不消耗任何 LLM token。

---

## 7. 范围外（Out of Scope）

- **调度型冷技能**（manifest 预留 `schedule` 字段，本期不实现调度挂载，scripts/ cron 脚本不迁移）。
- **Agent 答复回流**（post_llm_call hook 捕获 Agent 回答、praise 关联蒸馏 FAQ）——挂起，等 P1/P2 数据说话后再评估，是唯一重新引入 token 的环节。
- **核心 intents 重构/迁移**为冷技能格式（渐进固化内置除外，且需单独审批）。
- literal/attach 之外的新领域意图（需要新 handler 的，仍走正常开发流程，不是采纳命令的事）。

---

## 8. 风险与护栏

- **留痕隐私**：原始消息落盘本地文件，与 memory.jsonl 同等防护等级，不对外传输。
- **热加载并发**：Secretary 单 daemon 模型，无多进程写冲突；loader 用 mtime 缓存。
- **技能仓库版本漂移**：manifest 带 schema 版本号字段，loader 升级时兼容旧版本或明确拒绝并提示升级。
- **采纳误操作**：撤销命令兜底；仓库 git 历史可完整回溯。
- **候选噪音**：仅 allow 路径且同原文≥3 次才晋级；高频但已命中 handle 的消息不进候选。

---

## 9. 开放问题（若实现中遇到再拍板）

- 候选清单容量上限与"已完成候选"清理策略。
- 技能仓库是否加 CI（校验 manifest + 跑 script 测试）——建议仓库建立后立即加，属于仓库侧小工程。
- attach 模式关键词与核心意图关键词冲突时（同名）的优先裁决（建议：核心意图优先，attach 别名仅作补充）。