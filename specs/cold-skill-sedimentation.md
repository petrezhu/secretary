# [SPEC] ColdSkill 冷技能主动沉淀机制

## Problem Statement

Secretary 的目标是"沉淀式智能体"：用规则引擎处理确定性业务，用数据积累替代 LLM 推理，越用越精准、越用越省 token。但目前沉淀闭环没有建立：

- 只有被规则引擎拦截的消息会留痕，流入 Agent 的消息零记录。用户反复追问的确定性问题是沉淀的富矿，却全部蒸发，永远无法变成规则。
- 规则引擎的关键词写死在核心代码里。新增一个能力意味着改代码、同步插件副本、重启服务——没有数据驱动的规则演进通道。
- 即使能挖出高频问询，也缺少严格的、可版本管理的、0 token 的能力单元格式去承接——散脚本无法自描述、无法被校验、无法追踪演进。

用户需要一个闭环：高频问题被自动发现 → 用户一句话采纳 → 沉淀为可版本管理的能力 → 立即且零 token 生效。

## Solution

建立"问询留痕 → 候选挖掘 → 人工采纳 → 冷技能生效"的主动沉淀闭环，并引入 ColdSkill（冷技能）作为 0 token 能力单元：

1. **留痕（P1）**：所有流入的消息全量记录，包括被 Agent 处理的路径。
2. **挖掘（P1）**：规则聚合出高频未命中原话候选清单——全程零 LLM。
3. **采纳（P2）**：用户用自然语言命令把候选沉淀为 ColdSkill。用户级冷技能存放在独立 git 仓库，运行时热加载，采纳立即生效。
4. **ColdSkill 契约（P2）**：三种模式——literal（关键词→固定文案）、attach（关键词挂靠到已有意图）、script（确定性脚本，必须带测试，由人类开发者编写）。新技能条目自动出现在帮助菜单"🧊 冷技能"分组。

## User Stories

留痕：

1. As a 用户, I want 所有发给秘书的消息都被记录下来（包括被 Agent 处理的那些）, so that 高频问题不会蒸发,能成为规则进化的原料。
2. As a 用户, I want 超长消息（超过意图层字长上限）也被留痕, so that 没有一条消息漏出记录。
3. As a 用户, I want 任意消息被拦截、数据缺失走异步补答、或放行给 Agent 三种结局都被留痕, so that 记录的"结局"字段能区分后续挖掘口径。
4. As a 用户, I want 留痕文件能自动轮转, so that 日志不会无限膨胀。

挖掘：

5. As a 用户, I want 同一句原文在短期内被 Agent 处理多次时被自动识别为候选, so that 我不需要手动统计自己说过什么。
6. As a 用户, I want 只有频繁且从未被规则命中的消息才成为候选, so that 已被规则服务的消息不产生噪音。
7. As a 用户, I want 候选使用我的原话原文（不做归一化）, so that 采纳后的关键词天然符合精确全等匹配纪律,不会误伤包含该词的其他句子。
8. As a 用户, I want 挖掘阈值、统计周期、候选有效期可配置, so that 不同沉淀节奏可以调校。

查看与采纳：

9. As a 用户, I want 输入"建议"就能看到当前候选清单, so that 我看到的是积累的结果而不是被推送打扰。
10. As a 用户, I want 输入"采纳建议#N 答复：xxx"能把候选沉淀为固定答复技能, so that 以后再问这句话秘书直接答复,零 token。
11. As a 用户, I want 输入"采纳建议#N 挂到 持仓"能把候选原话挂靠到已有意图, so that 已存在的处理逻辑直接复用,不造重复技能。
12. As a 用户, I want 采纳立即生效（不需要重启）, so that 沉淀的收益即时可见。
13. As a 用户, I want 输入"技能列表"能看到当前所有冷技能, so that 我清楚自己沉淀了什么。
14. As a 用户, I want 输入"撤销技能 X"能立即移除某个技能, so that 误采纳可即时回滚。
15. As a 用户, I want 撤销后的技能在 git 历史中可回溯, so that 误删也能找回。

ColdSkill 契约与加载：

16. As a 用户, I want 我的冷技能存放在独立 git 仓库并由 git 版本管理, so that 每次采纳/撤销都有提交历史和远端备份。
17. As a 用户, I want 别人（未来的我）能通过 manifest 读懂每个技能是什么、触发词是什么, so that 技能自描述,不依赖记忆。
18. As a 用户, I want 非法 manifest 的技能被拒绝加载且不影响核心功能, so that 一个坏技能不会带崩整个秘书。
19. As a 用户, I want 冷技能仓库缺失或损坏时秘书核心功能照常工作, so that 沉淀层永远 fail-open。
20. As a 用户, I want script 模式技能必须通过入口签名校验并自带测试, so that 手写冷逻辑有最低质量护栏。
21. As a 用户, I want literal 和 attach 模式不需要写测试, so that 机器模板生成的简单技能不被过度工程拖累。
22. As a 用户, I want 冷技能与核心意图同名时核心意图优先, so that 沉淀永远不覆盖内置能力。
23. As a 用户, I want 采纳时若技能代码引用了 LLM 接口只收到提示而不被拒绝, so that 冷/热边界是软约束,由人做最终判断。

帮助菜单与一致性：

24. As a 用户, I want 新采纳的技能自动出现在帮助菜单"🧊 冷技能"分组, so that 菜单反映真实能力,不需要手改菜单代码。
25. As a 用户, I want 所有沉淀链路（留痕、挖掘、加载、路由、菜单渲染）零 LLM 消耗, so that 沉淀机制本身不违背"越用越省 token"的初衷。
26. As a 用户, I want 采纳生成的技能同样遵守精确全等匹配, so that 规则引擎的匹配纪律在沉淀物上保持一致。

运维：

27. As a 用户, I want 采纳命令在 git push 失败时仍能本地生效并明确告知原因, so that 网络问题不阻塞能力上线。
28. As a 用户, I want 挖掘 job 挂在现有调度器上（与早间简报同机制）, so that 不引入新的进程模型。

## Implementation Decisions

### 留痕

- 在 gateway/inbound 模块的入站处理函数内新增记录逻辑，覆盖三条出口路径：超长消息早退分支、意图命中（high）、意图缺失/异步补答（medium）、放行 Agent（low）。所有分支都记录。
- 记录结构：归一化原文（首 80 字符）、命中意图名或"allow"、时间戳，追加写入 JSONL 文件（数据目录下）。
- 轮转策略沿用既有 handled-ledger 的 256KB 轮转。

### 候选挖掘

- 新增独立纯函数聚合模块 + 一个挂到现有 Scheduler 的周期性 Job（默认每周执行）。
- 聚合规则（全部零 LLM）：同一原文出现 ≥3 次/7 天且每次都走 allow 路径 → 候选。候选条目含原文、次数、最近时间、建议动作类型。输出 JSON 文件，仅纳入最近 N 天（默认 30）。
- 阈值、周期、天数均可配置。

### ColdSkill 契约

- 三模式：literal（manifest + 内嵌固定答复，免测试）、attach（manifest 声明目标意图 + 追加关键词，免测试）、script（manifest + 脚本入口 `run(ctx) -> str | None`，必须带测试文件）。
- manifest schema（来自设计原型，字段决策以此为准）：

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
llm_allowed: false          # 自述字段；不强制（软约束）
```

- 采纳命令只生成 literal 与 attach（模板化产出）；script 由人类开发者编写，用于逐步"固化内置"或复杂冷逻辑。

### 冷技能仓库与加载器

- 用户级冷技能 = 独立 git 仓库（不进 data dir、不混入本 repo），远端 Forgejo 主 + GitHub 备份；运行路径由环境变量定位。
- 新增 ColdSkillLoader：构建意图注册表后扫描技能目录，把技能包装为 handler 合并进注册表。核心意图与分发逻辑零改动。
- 校验：manifest schema 静态校验（硬）；script 模式入口签名断言（硬）；加载失败/仓库缺失一律 fail-open，冷技能层静默降级，绝不影响核心功能。
- 热加载：mtime 缓存 + 一次性失效，采纳/撤销后无需重启即生效。

### 采纳/撤销意图

- 新增四个意图（keywords + handle，与现有意图同构）：
  - "建议"：拉取候选清单。
  - "采纳建议#N 答复：xxx"：literal 模式。
  - "采纳建议#N 挂到 <意图>"：attach 模式（关键词别名追加到目标核心意图）。
  - "技能列表"：仓库现状。
  - "撤销技能 <id>"：移除并推送。
- 采纳即生效 + 撤销兜底；git push 失败不阻塞本地生效，回复中说明原因。

### 帮助菜单

- 复用现有 registry 驱动的动态菜单渲染机制（帮助处理器已有注册表引用），新增"🧊 冷技能"分组，从 manifest description + keywords 渲染，菜单零手写改动。

### 软约束

- 采纳时对 script 代码做 AST 扫描探测 LLM API 引用：仅输出提示建议，不拒绝加载。

## Testing Decisions

- **好测试的定义**：只断言外部行为——文件内容（留痕 JSONL、候选 JSON、技能仓库 git 状态）、路由结果（dispatch 三层响应）、用户可见文本（能力输出/帮助菜单内容）。不测私有字节实现。
- **留痕**：构造三类入站请求（超长、命中、放行）断言 JSONL 追加内容。
- **挖掘**：构造 queries.jsonl fixture，断言阈值、周期、去噪（非 allow 不进候选、低于阈值不进候选）。
- **加载器**：literal/attach 正常加载、非法 manifest 拒绝加载且核心链路不断、仓库缺失 fail-open、改动后热生效。
- **采纳意图**：golden set 路由 + 行为级断言（技能目录生成、"挂到"后目标意图关键词扩展）。
- **帮助菜单**：采纳后菜单渲染含新技能条目。
- **测试先例**：inbound 相关沿 test_gateway 的 FastAPI TestClient 模式；意图沿 test_intents / golden set 模式。
- **回归基线**：既有 98+ 测试全量保持通过。

## Out of Scope

- 帮助菜单展示层"主动丰富"机制三（使用榜、候选区、晚间复盘带候选计数），用户明确未批准。
- 调度型冷技能（日程触发能力单元）；脚本 cron 迁移。
- Agent 答复回流与 FAQ 蒸馏（唯一重新引入 token 的环节，待 P1/P2 数据验证后另行评估）。
- 核心 intents 重构为冷技能格式。
- 新领域意图（需要新处理逻辑的，走正常开发，不属于采纳命令职权）。
- 冷技能仓库 CI（建议仓库建立后单独小工程跟进）。

## Further Notes

- 冷技能仓库需新建并配置 Forgejo + GitHub 双远端；技能命名：snake_case id、语义分组目录。
- 候选清单的容量上限与完结清理策略、attach 与核心意图关键词冲突的优先裁决方案，实现中遇到再拍板。
- ColdSkill 与 Hermes skill 语义边界（前者面向规则引擎、零 token；后者面向 LLM、进 prompt）需在所有文档与命名中保持清晰，避免混淆。