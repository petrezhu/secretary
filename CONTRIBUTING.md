# Contributing to Secretary

感谢你对 Secretary 项目的关注！

## 开发环境

```bash
git clone https://github.com/petrezhu/secretary.git
cd secretary
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 运行测试

```bash
pytest                          # 全部测试
pytest tests/unit/              # 仅单元测试
pytest --cov=src/secretary      # 带覆盖率
```

## 代码风格

- **Linter**: ruff（`ruff check src/ tests/`）
- **格式**: 100 字符行宽，Python 3.10+
- **类型**: 优先使用 type hints

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: 新功能
fix: 修复
docs: 文档
test: 测试
refactor: 重构
chore: 构建/CI
```

## Issue 与 PR

- 使用 Issue 模板提交 bug 报告或功能请求
- PR 请关联相关 Issue
- 确保 CI 通过后再请求 review

## 架构

Secretary 是**主动式**个人助理 daemon，核心理念：

- **规则优先**: 确定性业务用规则引擎，不消耗 LLM token
- **沉淀式智能**: 越用越精准，数据积累替代推理
- **fail-open**: 任何组件故障不影响核心流程

详见 `docs/SPEC.md` 和 `CONTEXT.md`。
