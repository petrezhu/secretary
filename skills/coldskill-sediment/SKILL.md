---
name: coldskill-sediment
description: "Sediment a conversation into a Secretary ColdSkill (0-token capability unit). Use when the user says 沉淀/沉淀为coldskill/沉淀为冷技能 in a QQ or chat session."
---

# ColdSkill Sedimentation — 对话沉淀为冷技能

将一段对话中反复出现的确定性 Q&A 沉淀为 Secretary ColdSkill，立即在 QQ 规则引擎生效（0 token，LLM 永不接触运行时）。

## 触发词

- `沉淀为coldskill` / `沉淀为冷技能` / `沉淀`（QQ 对话或本会话中）
- 可指定范围：`沉淀上面的X`（X = 话题关键词，如"沉淀上面的金价"）

## 取材规则（用户已拍板）

- 默认取**最近一轮 Q&A**（用户说"沉淀"即沉淀刚才那段对话）
- `沉淀上面的X` → 回扫会话，找 X 相关的最近一组问答
- 不要求用户粘贴原文；找不到明确材料时问一次"沉淀哪一段？"，仍不明确则终止

## 生成模式（三选一）

先判断材料性质，再选模式。**生成什么由对话内容决定，由你（Agent）完成全部工作。**

| 模式 | 适用材料 | 产物 |
|------|---------|------|
| `literal` | 固定答案的问答（FAQ、"X是什么"） | skill.json（含 replies） |
| `attach` | 答案已有核心意图承载，只差触发词 | skill.json（target + keywords） |
| `script` | 需要确定性计算/实时数据（价格、换算、日期计算、查表） | skill.json + script.py + test_script.py |

核心意图清单（attach 的合法 target）：`portfolio` `market` `qdii` `gold` `task_status` `task_create` `task_complete` `task_detail` `today_focus` `longterm_goals` `inbox` `system_health` `checkpoint` `morning_briefing` `system_registry` `memory_query` `memory_add` `memory_pending` `help`。

**Agent 允许生成 script 模式**（用户 2026-09-16 决策），但必须满足测试硬门（见下）。

**模式判断例**："第几周" 答案随时间变化 → 是计算不是固定文案 → script 模式，不是 literal。

## ⛔ 三条铁律（2026-09-16 实战教训，违反即伪沉淀）

1. **产物进 cold-skills 仓库，不是 Hermes 技能目录。**
   禁止用 `skill_manage` 创建 ColdSkill——那是 Hermes 热技能（进 prompt 烧 token），
   与 ColdSkill（0 token，规则引擎执行）是两个物种。伪产物示例：
   `/root/.hermes/profiles/main/skills/<id>/SKILL.md` ← **永远不是这里**
   正确产物：`$SECRETARY_COLD_SKILLS_DIR/<id>/skill.json` (+ script.py)

2. **id 必须 snake_case。** `^[a-z][a-z0-9_]*$`。`week-number-query` 会被 loader
   拒载（`id not snake_case, skipped`），用 `week_number_query`。写完 skill.json
   先本地自检：`python3 -c "import json,re; m=json.load(open('skill.json')); assert re.match(r'^[a-z][a-z0-9_]*$', m['id'])"`

3. **必须 curl 验证生效后才能报告成功。** 写入+push ≠ 生效。提交后跑：
   ```bash
   curl -s -X POST http://127.0.0.1:8901/api/inbound -H "Content-Type: application/json" \
     -d '{"text":"<关键词>","user_id":"sediment_test","chat_id":"sediment_test"}'
   ```
   `action=handle` 且回复正确 → 才能说"已创建"。验证失败时查
   `journalctl -t secretary -n 20`（ColdSkill skipped 行即拒载原因）。
   2026-09-16 事故：Agent 未验证即宣布成功，用户实测放行 Agent，伪沉淀。

## 关键词纪律（硬约束）

- 关键词是**精确全等匹配**集合，不是子串匹配
- 用**用户原话**作为关键词首选（与候选挖掘的纪律一致）
- 每个技能 1–5 个关键词，宁少勿多
- 禁止与核心意图已有关键词重叠（重叠会被核心意图抢先，技能永不生效）

## 仓库布局与 manifest

仓库根：`$SECRETARY_COLD_SKILLS_DIR`（默认 `/root/git/secretary-cold-skills`，缺省时先 `git init`，见安装节）。

```
<skill-id>/          # snake_case，语义化命名
  skill.json         # manifest（schema 见下）
  script.py          # 仅 script 模式
  test_script.py     # 仅 script 模式（必须，与 script.py 同目录）
```

```json
{
  "id": "gold-faq",
  "version": 1,
  "mode": "literal",
  "description": "一行描述，直接进帮助菜单",
  "keywords": ["用户原话A", "用户原话B"],
  "replies": ["固定答复……"],
  "enabled": true,
  "tags": ["财富", "faq"]
}
```

attach 模式用 `"target": "portfolio"` 替代 `replies`；script 模式用 `"handler": "script.py", "entry": "run"`。`id` 必须 `^[a-z][a-z0-9_]*$`。

## script 模式契约

```python
# script.py — 确定性逻辑，禁止 import 任何 LLM SDK
def run(ctx) -> str | None:
    """ctx: 有 .text (str) 属性。返回答复文本，None = 放行 Agent。"""
```

- 零 LLM 依赖（硬规则：出现 `openai`/`anthropic`/`dashscope` 等 import 直接重写）
- 网络调用必须有 timeout，失败返回 None（fail-open）
- `test_script.py` 用 pytest，覆盖：正常路径、边界输入、失败返回 None

## 测试硬门（Q7=A，script 模式强制）

生成后、提交前，在技能目录执行：

```bash
cd "$SECRETARY_COLD_SKILLS_DIR/<skill-id>" && python3 -m pytest test_script.py -q
```

- **通过** → 允许 commit + push
- **失败** → 修复重试，最多 2 次；仍不过则删除该技能目录，向用户说明原因，**绝不带病上线**
- literal / attach 模式免测试（无代码可测）

## 写入与生效流程

1. 确定模式与关键词（对照上方纪律自查）
2. 写 `skill.json`（+ script 文件）
3. script 模式：跑测试硬门
4. 提交双推：

```bash
cd "$SECRETARY_COLD_SKILLS_DIR"
git add -A && git commit -m "feat: <skill-id> — <一句话>"
git push origin main 2>&1 | tail -1    # Forgejo 主
git push github main 2>&1 | tail -1    # GitHub 备份（失败不阻塞）
```

5. **验证生效**（loader 是 mtime 热加载，无需重启任何服务）：

```bash
curl -s -X POST http://127.0.0.1:8901/api/inbound \
  -H "Content-Type: application/json" \
  -d '{"text":"<关键词之一>","user_id":"sediment_test","chat_id":"sediment_test"}'
```

返回 `action=handle` 且回复符合预期 → 成功，向用户报告生效结果与帮助菜单入口。

## 撤销兜底

用户说"撤销技能 X"：QQ 直接发即可（Secretary 内置意图）；或本会话内 `rm -rf <skill-id>/` + commit + push。失误可即时回滚。

## 安装（install.sh 已自动化）

- Skill 本体装到 Hermes：`<skills_dir>/software-development/coldskill-sediment/`
- 冷技能仓库：`SECRETARY_COLD_SKILLS_DIR` 不存在时 install.sh 自动 `git init` + 生成 README（不带 remote，双推地址由用户自加）
- 手动安装：把本目录复制到上述 skills_dir 即可

## 失败模式速查

| 症状 | 原因 | 处置 |
|------|------|------|
| curl 验证 action=allow | id 非 snake_case / keywords 与核心意图重叠 / manifest 校验失败 | 查 `journalctl -t secretary` 的 ColdSkill skipped 行，逐条修 |
| 技能列表看不到 | 同上，schema 校验失败（id/version/mode/handler） | 对照 manifest 契约逐字段查 |
| script 不执行 | 缺 test_script.py 或 run 签名错 | loader 硬约束，补文件 |
| push 失败 | 无 remote / 网络 | 本地已生效，告知用户稍后手动推 |
