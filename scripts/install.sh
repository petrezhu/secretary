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
echo "[4/4] Verifying installation..."
if secretary check --json > /dev/null 2>&1; then
    echo "✓ secretary CLI works"
else
    echo "⚠ secretary CLI check returned warnings (this is OK for first install)"
fi

echo ""
echo "=== Installation Complete ==="
echo "Don't forget to:"
echo "  1. cp .env.example .env"
echo "  2. Edit .env with your actual configuration"
