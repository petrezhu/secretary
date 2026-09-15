#!/bin/bash
set -euo pipefail

echo "=== Secretary Installation ==="

# Install Python package
echo "[1/4] Installing secretary package..."
cd "$(dirname "$0")/.."
pip install -e . --no-deps -q

# Create data directory
echo "[2/4] Creating data directory..."
SECRETARY_DATA_DIR="${SECRETARY_DATA_DIR:-./data}"
mkdir -p "$SECRETARY_DATA_DIR"

# Install systemd service (optional)
echo "[3/4] Installing systemd service..."
if [ -d /etc/systemd/system ]; then
    cp scripts/secretary.service /etc/systemd/system/secretary.service
    systemctl daemon-reload
    systemctl enable secretary 2>/dev/null || true
    echo "  systemd service installed"
else
    echo "  systemd not found, skipping service installation"
fi

# Verify
echo "[4/6] Verifying installation..."
if secretary check --json > /dev/null 2>&1; then
    echo "✓ secretary CLI works"
else
    echo "⚠ secretary CLI check returned warnings (this is OK for first install)"
fi

# Install ColdSkill sedimentation skill into Hermes (Q3=A: probe, don't hardcode)
echo "[5/6] Installing coldskill-sediment skill into Hermes..."
SKILL_SRC="$(dirname "$0")/../skills/coldskill-sediment"
if [ ! -d "$SKILL_SRC" ]; then
    echo "  ⚠ skill source missing, skipping"
else
    # Locate Hermes home: $HERMES_HOME > hermes config > default
    HERMES_HOME_CAND="${HERMES_HOME:-}"
    if [ -z "$HERMES_HOME_CAND" ]; then
        HERMES_HOME_CAND="$(hermes config get hermes_home 2>/dev/null || true)"
    fi
    if [ -z "$HERMES_HOME_CAND" ] && [ -d "$HOME/.hermes" ]; then
        HERMES_HOME_CAND="$HOME/.hermes"
    fi
    if [ -n "$HERMES_HOME_CAND" ] && [ -d "$HERMES_HOME_CAND" ]; then
        SKILL_DST="$HERMES_HOME_CAND/skills/software-development/coldskill-sediment"
        mkdir -p "$(dirname "$SKILL_DST")"
        rm -rf "$SKILL_DST"
        cp -r "$SKILL_SRC" "$SKILL_DST"
        echo "  ✓ installed to $SKILL_DST"
    else
        echo "  ⚠ Hermes home not found, skill not installed"
    fi
fi

# Initialize cold-skills repo if missing (Q8=A: git init + README, no remote)
echo "[6/6] Ensuring cold-skills repo exists..."
COLD_DIR="${SECRETARY_COLD_SKILLS_DIR:-/root/git/secretary-cold-skills}"
if [ -d "$COLD_DIR/.git" ]; then
    echo "  ✓ $COLD_DIR already a git repo"
else
    mkdir -p "$COLD_DIR"
    if git init -q "$COLD_DIR" 2>/dev/null; then
        if [ ! -f "$COLD_DIR/README.md" ]; then
            cat > "$COLD_DIR/README.md" <<'README_EOF'
# Secretary Cold Skills

User-level cold skills for Secretary — 0-token deterministic capability
units executed by the rule engine. Managed by the `coldskill-sediment`
skill (conversation sedimentation) and the built-in 建议/采纳 intents.

Layout: `<skill-id>/skill.json` (+ `script.py` + `test_script.py` for script mode).
Loader contract: see Secretary docs / `SECRETARY_COLD_SKILLS_DIR` env var.
README_EOF
        fi
        git -C "$COLD_DIR" add -A
        git -C "$COLD_DIR" commit -q -m "init: cold-skills repo bootstrap" 2>/dev/null || true
        echo "  ✓ initialized $COLD_DIR (add Forgejo + GitHub remotes for双推)"
    else
        echo "  ⚠ git init failed, loader will run fail-open without skills"
    fi
fi

echo ""
echo "=== Installation Complete ==="
echo "Don't forget to:"
echo "  1. cp .env.example .env"
echo "  2. Edit .env with your actual configuration"
echo "  3. (optional) Add remotes to $COLD_DIR for git 双推"
