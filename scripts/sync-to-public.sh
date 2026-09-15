#!/usr/bin/env bash
# sync-to-public.sh — 单向同步: private repo → public repo (脱敏)
#
# 用法: ./scripts/sync-to-public.sh [--dry-run]
#
# 工作原理:
#   1. rsync 源码文件从 private → public 目录
#   2. 跳过敏感文件 (.env, config/local.*, *.db, scripts/em_*)
#   3. 保留 public-only 文件 (CONTRIBUTING.md, .github/* 等)
#   4. 提交并推送到 GitHub + Forgejo
#
# 前提:
#   - public repo 已 clone 到 SECRETARY_PUBLIC_DIR
#   - GitHub/Forgejo 认证已配置

set -euo pipefail

PRIVATE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_DIR="${SECRETARY_PUBLIC_DIR:-/root/git/secretary-public}"
DRY_RUN="${1:-}"

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "[DRY-RUN] Would sync $PRIVATE_DIR → $PUBLIC_DIR"
    DRY=1
else
    DRY=0
fi

# ── 验证目录 ──────────────────────────────────────────────
if [[ ! -d "$PRIVATE_DIR/.git" ]]; then
    echo "ERROR: $PRIVATE_DIR is not a git repo"
    exit 1
fi
if [[ ! -d "$PUBLIC_DIR/.git" ]]; then
    echo "ERROR: $PUBLIC_DIR is not a git repo. Clone it first."
    exit 1
fi

# ── rsync (排除敏感文件 + public-only 文件) ───────────────
RSYNC_OPTS=(
    -av --delete
    --exclude='.git'
    --exclude='.env'
    --exclude='*.env.local'
    --exclude='*.db'
    --exclude='*.db-journal'
    --exclude='*.db-wal'
    --exclude='*.db-shm'
    --exclude='__pycache__'
    --exclude='.pytest_cache'
    --exclude='.ruff_cache'
    --exclude='*.egg-info'
    --exclude='dist'
    --exclude='build'
    --exclude='.eggs'
    --exclude='*.pyc'
    --exclude='audit.jsonl'
    --exclude='.hermes'
    --exclude='node_modules'
    --exclude='config/local.json'
    --exclude='config/local_stocks.json'
    --exclude='scripts/em_*.py'
    # public-only 文件（不从 private 同步，不删除）
    --exclude='CONTRIBUTING.md'
    --exclude='CHANGELOG.md'
    --exclude='.github/'
    # 脱敏模板（public 有独立版本，不覆盖）
    --exclude='AGENTS.md'
    --exclude='docs/agents/issue-tracker.md'
    --exclude='docs/agents/triage-labels.md'
)

if [[ $DRY -eq 1 ]]; then
    RSYNC_OPTS+=(-n)
fi

echo "[SYNC] $PRIVATE_DIR → $PUBLIC_DIR"
rsync "${RSYNC_OPTS[@]}" "$PRIVATE_DIR/" "$PUBLIC_DIR/"

# ── 提交 & 推送 ──────────────────────────────────────────
if [[ $DRY -eq 1 ]]; then
    echo "[DRY-RUN] Would commit and push"
    exit 0
fi

cd "$PUBLIC_DIR"
git add -A

if git diff --cached --quiet; then
    echo "[OK] No changes to sync"
    exit 0
fi

# 生成 commit message
CHANGED=$(git diff --cached --stat | tail -1)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "sync: $TIMESTAMP — $CHANGED"

# 推送到双远端
echo "[PUSH] GitHub..."
git push origin main 2>&1 || echo "[WARN] GitHub push failed (network?)"

echo "[PUSH] Forgejo..."
git push forgejo main 2>&1 || echo "[WARN] Forgejo push failed"

echo "[DONE] Synced successfully"
