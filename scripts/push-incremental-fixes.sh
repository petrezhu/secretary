#!/usr/bin/env bash
# =============================================================================
# push-incremental-fixes.sh — 把本地仓库里的“增量修复”推送到 GitHub（不带本地私有 Git 历史）
#
# 背景/动机:
#   GitHub 远端是一份“不含隐私信息提交”的干净版本。本地仓库的 Git 历史（bundle 迁移来的）
#   可能夹带隐私/不该公开的提交，所以**绝不能**用本地 HEAD 强推覆盖远端。
#   本脚本只提取指定修复提交**改动的文件内容**，覆盖到 GitHub main 的干净快照上，
#   以“干净的增量提交”推回远端 —— 不携带任何本地 Git 历史。
#
# 方法（文件级内容同步，而非 git apply 上下文补丁）:
#   本地修复提交构建在 bundle 历史上，父版本文件与 GitHub main 差异大，git apply 会冲突
#   （3-way 也未必命中）。因此对每个修复提交，取其“修改(M)/新增(A)”文件列表，
#   把本地**修复后**的文件内容复制到干净克隆，再提交 + 推送。
#   这样远端拿到“修复后状态”的增量，干净、无历史、无无关文件。
#
# 用法:
#   ./scripts/push-incremental-fixes.sh [--dry-run]
#
# 配置:
#   REPOS - "<GitHub仓库名>|<本地仓库路径>|<逗号分隔修复commit>"
#   GITHUB_BASE / SCRATCH 可改
# =============================================================================
set -euo pipefail

# 配置: "<仓库名>|<本地仓库路径>|<逗号分隔修复commit>"
REPOS=(
  "secretary|/root/git/secretary|821d043,a1d34c8,f1b4205"
  "secretary-gateway|/root/git/secretary-gateway|e7fb344,4e6e27c"
  "secretary-cold-skills|/root/git/secretary-cold-skills|e364cb4,ef9d921"
)
GITHUB_BASE="https://github.com/petrezhu"
SCRATCH="/tmp/inc-fix-push"
DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; fi

echo "=== 增量修复推送 (dry-run=$DRY) ==="

for entry in "${REPOS[@]}"; do
  IFS='|' read -r name local_path commits <<< "$entry"
  echo ""
  echo "########## 仓库: $name (本地: $local_path) ##########"

  if [[ ! -d "$local_path/.git" ]]; then
    echo "  [SKIP] 本地仓库不存在: $local_path"
    continue
  fi

  # ── 干净基线: 浅克隆 GitHub main ─────────────
  work="$SCRATCH/$name"
  rm -rf "$work"
  if ! git clone --depth=1 --branch main "$GITHUB_BASE/$name.git" "$work" 2>/tmp/incfix_clone.err; then
    echo "  [FAIL] 浅克隆 GitHub 失败: $(tail -1 /tmp/incfix_clone.err)"
    continue
  fi
  echo "  干净基线 (GitHub main): $(git -C "$work" rev-parse --short HEAD)"

  # ── 收集所有修复提交涉及的“修改/新增”文件 ─────
  #     用关联数组去重；删除的 .pyc/.cache 由远端现状决定（远端本无则忽略）
  declare -A files_to_sync=()
  for fix in ${commits//,/ }; do
    fix=$(echo "$fix" | tr -d ' ')
    if ! git -C "$local_path" cat-file -e "$fix^{commit}" 2>/dev/null; then
      echo "  [SKIP] 本地无此提交: $fix"
      continue
    fi
    while read -r st path; do
      [[ -z "$path" ]] && continue
      if [[ "$st" == "M" || "$st" == "A" ]]; then
        files_to_sync["$path"]=1
      fi
    done < <(git -C "$local_path" diff-tree --no-commit-id --name-status -r "$fix")
  done

  if [[ ${#files_to_sync[@]} -eq 0 ]]; then
    echo "  [SKIP] 无修改文件可同步"
    continue
  fi

  # ── 复制修复后文件内容到干净克隆 ─────────────
  echo "  待同步文件: ${!files_to_sync[*]}"
  for path in "${!files_to_sync[@]}"; do
    src="$local_path/$path"
    dst="$work/$path"
    mkdir -p "$(dirname "$dst")"
    if [[ -f "$src" ]]; then
      cp "$src" "$dst"
      echo "    ✓ 复制: $path"
    else
      echo "    (文件不存在于本地工作树, 跳过) $path"
    fi
  done

  # ── 提交 & 推送 ──────────────────────────────
  if [[ -z "$(git -C "$work" status --porcelain)" ]]; then
    echo "  [OK] 无改动(远端正本已包含这些文件), 无需提交"
    continue
  fi

  if [[ $DRY -eq 1 ]]; then
    echo "  [DRY-RUN] 将提交并推送 origin main"
    git -C "$work" status --short
    continue
  fi

  git -C "$work" add -A
  commits_msg=$(for fix in ${commits//,/ }; do git -C "$local_path" log -1 --pretty=%s "$(echo "$fix"|tr -d ' ')"; done | paste -sd '; ' -)
  git -C "$work" commit -q -m "fix: 增量推送 — $commits_msg"
  echo "  ✓ 已提交: $(git -C "$work" log -1 --oneline)"

  echo "  ↓ 推送到 GitHub main ..."
  if git -C "$work" push origin HEAD:main 2>/tmp/incfix_push.err; then
    echo "  ✓ PUSH 成功: https://github.com/petrezhu/$name"
  else
    echo "  [FAIL] push 失败: $(tail -3 /tmp/incfix_push.err)"
  fi
done

echo ""
echo "=== 完成 ==="
