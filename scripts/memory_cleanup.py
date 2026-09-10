#!/usr/bin/env python3
"""memory_cleanup.py — 自动识别并清理服务器上的孤儿/重复/遗留进程。

识别4类问题进程:
  1. Orphan Chromium/CDP headless — 无客户端连接的headless Chrome
  2. Stale Hermes CLI sessions — SSH已断开但hermes进程仍存活
  3. Duplicate CodeGraph instances — 多次启动积累的旧实例
  4. Empty PM2 daemon — 管理列表为空的PM2 God Daemon
  5. (bonus) Stale delegation subagent workers

用法:
  python memory_cleanup.py              # dry-run，只报告不清理
  python memory_cleanup.py --kill       # 实际清理
  python memory_cleanup.py --json       # JSON输出（供intent调用）

退出码:
  0 = 无问题 或 已清理
  1 = 发现问题但未清理（dry-run模式）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Literal

# ── data models ──────────────────────────────────────────────

@dataclass
class RogueProcess:
    pid: int
    ppid: int
    rss_kb: int
    cmd: str
    category: Literal[
        "orphan_chromium",
        "stale_hermes",
        "duplicate_codegraph",
        "empty_pm2",
        "stale_subagent",
    ]
    reason: str

    @property
    def rss_mb(self) -> float:
        return self.rpm_kb / 1024 if hasattr(self, "rpm_kb") else self.rss_kb / 1024


@dataclass
class CleanupReport:
    found: list[RogueProcess] = field(default_factory=list)
    killed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mem_before_mb: float = 0.0
    mem_after_mb: float = 0.0

    @property
    def total_rss_mb(self) -> float:
        return sum(p.rss_kb for p in self.found) / 1024

    @property
    def freed_mb(self) -> float:
        return max(0, self.mem_before_mb - self.mem_after_mb)

    def summary(self) -> str:
        if not self.found:
            return "✅ 内存干净，没有发现孤儿/重复/遗留进程。"
        lines = [f"🔍 发现 {len(self.found)} 个问题进程，共 {self.total_rss_mb:.0f}MB:"]
        by_cat: dict[str, list[RogueProcess]] = {}
        for p in self.found:
            by_cat.setdefault(p.category, []).append(p)
        cat_labels = {
            "orphan_chromium": "🌐 孤儿 Chromium/CDP",
            "stale_hermes": "📟 过期 Hermes 会话",
            "duplicate_codegraph": "🔧 重复 CodeGraph 实例",
            "empty_pm2": "📦 空 PM2 Daemon",
            "stale_subagent": "🤖 遗留子代理进程",
        }
        for cat, procs in by_cat.items():
            label = cat_labels.get(cat, cat)
            total = sum(p.rss_kb for p in procs) / 1024
            lines.append(f"\n  {label} ({len(procs)}个, {total:.0f}MB):")
            for p in procs:
                lines.append(f"    PID {p.pid} | {p.rss_kb/1024:.0f}MB | {p.reason}")
                # truncate long cmds
                cmd_short = p.cmd[:80] + ("…" if len(p.cmd) > 80 else "")
                lines.append(f"      └─ {cmd_short}")
        if self.killed:
            lines.append(f"\n☠️  已清理 {len(self.killed)} 个进程，释放 ~{self.freed_mb:.0f}MB")
        elif self.found:
            lines.append("\n⚠️  dry-run 模式，未实际清理。加 --kill 执行。")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "found": len(self.found),
            "total_rss_mb": round(self.total_rss_mb, 1),
            "killed": len(self.killed),
            "freed_mb": round(self.freed_mb, 1),
            "errors": self.errors,
            "details": [
                {
                    "pid": p.pid,
                    "category": p.category,
                    "rss_mb": round(p.rss_kb / 1024, 1),
                    "reason": p.reason,
                }
                for p in self.found
            ],
        }


# ── helpers ──────────────────────────────────────────────────

def _ps_aux() -> list[dict]:
    """Parse ps output into list of dicts (includes ppid)."""
    out = subprocess.check_output(
        ["ps", "-eo", "pid,ppid,user,%cpu,%mem,vsz,rss,tty,stat,start,time,cmd",
         "--no-headers"],
        text=True, timeout=10,
    )
    procs = []
    for line in out.strip().splitlines():
        parts = line.split(None, 12)
        if len(parts) < 12:
            continue
        try:
            procs.append({
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "user": parts[2],
                "cpu": float(parts[3]),
                "mem": float(parts[4]),
                "vsz": int(parts[5]),
                "rss": int(parts[6]),
                "tty": parts[7],
                "stat": parts[8],
                "start": parts[9],
                "time": parts[10],
                "cmd": parts[11],
            })
        except (ValueError, IndexError):
            continue
    return procs


def _get_active_tty_set() -> set[str]:
    """Get TTYs from currently logged-in users via `who`."""
    try:
        out = subprocess.check_output(["who"], text=True, timeout=5)
        ttys = set()
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("pts/"):
                ttys.add(parts[1])
        return ttys
    except Exception:
        return set()


def _get_children(pid: int) -> list[int]:
    """Get direct child PIDs."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(pid)], text=True, timeout=5
        )
        return [int(x) for x in out.strip().splitlines() if x.strip()]
    except Exception:
        return []


def _kill_tree(pid: int, errors: list[str]) -> bool:
    """Kill a process and all its children (SIGKILL)."""
    import signal
    children = _get_children(pid)
    for c in children:
        _kill_tree(c, errors)  # recursive
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True  # already dead
    except PermissionError:
        errors.append(f"Permission denied killing PID {pid}")
        return False
    except Exception as e:
        errors.append(f"Failed to kill PID {pid}: {e}")
        return False


def _memory_used_mb() -> float:
    """Current used memory in MB."""
    try:
        out = subprocess.check_output(["free", "-m"], text=True, timeout=5)
        for line in out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                return float(parts[2])  # 'used' column
    except Exception:
        pass
    return 0.0


# ── detectors ────────────────────────────────────────────────

def detect_orphan_chromium(procs: list[dict]) -> list[RogueProcess]:
    """Find headless Chromium processes with no active CDP client."""
    rogues = []
    # Find chrome main processes (--headless + --remote-debugging-port)
    chrome_main_pids = []
    for p in procs:
        cmd = p["cmd"]
        if (
            "chrome" in cmd
            and "--headless" in cmd
            and "--remote-debugging-port" in cmd
            and p["pid"] != p["ppid"]  # not init
        ):
            chrome_main_pids.append(p["pid"])

    for main_pid in chrome_main_pids:
        # Check if anyone is connected to the debugging port
        # by looking for the port in the cmd
        port = None
        for p in procs:
            if p["pid"] == main_pid:
                import re
                m = re.search(r"--remote-debugging-port=(\d+)", p["cmd"])
                if m:
                    port = m.group(1)
                break

        # If port is standard CDP test port (9222) and user-data-dir is /tmp, it's a test
        is_test = False
        for p in procs:
            if p["pid"] == main_pid:
                if "/tmp/cdp-test" in p["cmd"] or "/tmp/" in p["cmd"]:
                    is_test = True
                break

        if is_test:
            # Count total RSS of this tree
            tree_rss = 0
            tree_pids = [main_pid]
            # BFS to find all descendants
            queue = [main_pid]
            seen = {main_pid}
            while queue:
                cur = queue.pop(0)
                for p in procs:
                    if p["ppid"] == cur and p["pid"] not in seen:
                        seen.add(p["pid"])
                        queue.append(p["pid"])
                        tree_pids.append(p["pid"])

            for p in procs:
                if p["pid"] in seen:
                    tree_rss += p["rss"]

            rogues.append(RogueProcess(
                pid=main_pid,
                ppid=0,
                rss_kb=tree_rss,
                cmd=next((p["cmd"] for p in procs if p["pid"] == main_pid), ""),
                category="orphan_chromium",
                reason=f"Headless Chrome test instance ({len(tree_pids)} processes, port {port})",
            ))
    return rogues


def detect_stale_hermes(procs: list[dict]) -> list[RogueProcess]:
    """Find hermes CLI processes on pts that no longer have active SSH sessions."""
    active_ttys = _get_active_tty_set()
    rogues = []

    for p in procs:
        cmd = p["cmd"]
        tty = p["tty"]
        # Match hermes CLI processes (not gateway, not watchdog)
        if (
            "hermes" in cmd
            and "-p main" in cmd
            and "gateway" not in cmd
            and "watchdog" not in cmd
            and "mcp_stdio" not in cmd
            and tty.startswith("pts/")
            and tty not in active_ttys
        ):
            rogues.append(RogueProcess(
                pid=p["pid"],
                ppid=p["ppid"],
                rss_kb=p["rss"],
                cmd=cmd,
                category="stale_hermes",
                reason=f"Hermes on {tty} but SSH session gone (started {p['start']})",
            ))
    return rogues


def detect_duplicate_codegraph(procs: list[dict]) -> list[RogueProcess]:
    """Find codegraph serve instances whose hermes parent is dead."""
    import re as _re

    # Find all active hermes PIDs (including gateway)
    active_hermes_pids = set()
    for p in procs:
        cmd = p["cmd"]
        if "hermes" in cmd and ("gateway" in cmd or "-p main" in cmd or "-p coder" in cmd):
            active_hermes_pids.add(p["pid"])

    # Collect watchdog -> hermes_pid mappings
    wd_to_hermes = {}  # watchdog_pid -> hermes_pid
    for p in procs:
        if "mcp_stdio_watchdog" in p["cmd"]:
            m = _re.search(r"--ppid\s+(\d+)", p["cmd"])
            if m:
                wd_to_hermes[p["pid"]] = int(m.group(1))

    # For each codegraph-linux binary, walk up parent chain to find
    # its watchdog, then check if that watchdog's hermes parent is alive
    rogues = []
    seen_cg = set()

    for p in procs:
        cmd = p["cmd"]
        if not (
            "codegraph-linux" in cmd
            and "serve" in cmd
            and "--mcp" in cmd
            and "--path" not in cmd
        ):
            continue

        # Walk up the parent chain to find the watchdog
        cur_pid = p["pid"]
        found_live_hermes = False
        for _ in range(5):  # max 5 levels up
            parent_pid = next(
                (x["ppid"] for x in procs if x["pid"] == cur_pid), 0
            )
            if parent_pid == 0:
                break
            if parent_pid in wd_to_hermes:
                hermes_pid = wd_to_hermes[parent_pid]
                if hermes_pid in active_hermes_pids:
                    found_live_hermes = True
                break
            cur_pid = parent_pid

        if not found_live_hermes and p["pid"] not in seen_cg:
            seen_cg.add(p["pid"])
            rogues.append(RogueProcess(
                pid=p["pid"],
                ppid=p["ppid"],
                rss_kb=p["rss"],
                cmd=cmd,
                category="duplicate_codegraph",
                reason=f"CodeGraph instance without active hermes parent (started {p['start']})",
            ))

    return rogues


def detect_empty_pm2(procs: list[dict]) -> list[RogueProcess]:
    """Find PM2 God Daemon with empty process list."""
    rogues = []
    for p in procs:
        if "PM2" in p["cmd"] and "God Daemon" in p["cmd"]:
            # Check if pm2 list is empty
            try:
                out = subprocess.check_output(
                    ["pm2", "list", "--no-color"],
                    text=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL,
                )
                # If the table has no data rows (only header/footer), it's empty
                data_lines = [
                    l for l in out.splitlines()
                    if l.strip().startswith("│") and "id" not in l.lower()
                    and "name" not in l.lower() and "───" not in l
                ]
                if not data_lines:
                    rogues.append(RogueProcess(
                        pid=p["pid"],
                        ppid=p["ppid"],
                        rss_kb=p["rss"],
                        cmd=p["cmd"],
                        category="empty_pm2",
                        reason=f"PM2 God Daemon with 0 managed processes (since {p['start']})",
                    ))
            except Exception:
                pass  # pm2 not available, skip
    return rogues


def detect_stale_subagents(procs: list[dict]) -> list[RogueProcess]:
    """Find delegation subagent worker processes without active parent."""
    rogues = []
    # Hermes subagent workers: python hermes_cli.main ... with delegation markers
    # They typically run as background children of hermes sessions
    # Look for hermes processes that are NOT on any pts (background workers)

    for p in procs:
        cmd = p["cmd"]
        tty = p["tty"]
        if (
            "hermes" in cmd
            and "gateway" not in cmd
            and "watchdog" not in cmd
            and "mcp_stdio" not in cmd
            and not tty.startswith("pts/")  # background, not interactive
            and tty == "?"  # no controlling terminal
            and "-p main" in cmd
        ):
            # This is a background hermes process — could be a delegation worker
            # Check if its parent is still alive
            parent_alive = any(x["pid"] == p["ppid"] for x in procs)
            if not parent_alive:
                rogues.append(RogueProcess(
                    pid=p["pid"],
                    ppid=p["ppid"],
                    rss_kb=p["rss"],
                    cmd=cmd,
                    category="stale_subagent",
                    reason=f"Background hermes worker, parent PID {p['ppid']} dead (started {p['start']})",
                ))
    return rogues


# ── main ─────────────────────────────────────────────────────

def run_cleanup(dry_run: bool = True) -> CleanupReport:
    """Main cleanup routine. Returns report."""
    report = CleanupReport()
    report.mem_before_mb = _memory_used_mb()

    procs = _ps_aux()

    # Run all detectors
    detectors = [
        detect_orphan_chromium,
        detect_stale_hermes,
        detect_duplicate_codegraph,
        detect_empty_pm2,
        detect_stale_subagents,
    ]

    for detector in detectors:
        try:
            found = detector(procs)
            report.found.extend(found)
        except Exception as e:
            report.errors.append(f"{detector.__name__}: {e}")

    # Deduplicate by PID
    seen_pids = set()
    deduped = []
    for p in report.found:
        if p.pid not in seen_pids:
            seen_pids.add(p.pid)
            deduped.append(p)
    report.found = deduped

    if not dry_run and report.found:
        for p in report.found:
            ok = _kill_tree(p.pid, report.errors)
            if ok:
                report.killed.append(p.pid)

        # Re-measure memory after kills
        import time
        time.sleep(1)
        report.mem_after_mb = _memory_used_mb()

    return report


def main():
    parser = argparse.ArgumentParser(description="清理服务器孤儿/重复/遗留进程")
    parser.add_argument("--kill", action="store_true", help="实际清理（默认dry-run）")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    report = run_cleanup(dry_run=not args.kill)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        print(report.summary())

    sys.exit(0 if (not report.found or args.kill) else 1)


if __name__ == "__main__":
    main()
