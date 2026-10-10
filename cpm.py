#!/usr/bin/env python3
"""cpm — 盘点 Claude Code 的 plugin / skill / MCP 资产，检查并执行更新。

设计见 docs/superpowers/specs/2026-10-10-cpm-design.md。
网络(fetch_url)与子进程(run_cmd)收敛为模块级函数，测试注入替换后可全离线运行。
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

EXIT_OK, EXIT_OUTDATED, EXIT_PARTIAL, EXIT_BROKEN = 0, 1, 2, 3


class Broken(Exception):
    """状态文件缺失/JSON 损坏（spec §6：立即退出，码 3，不做猜测性修复）。"""


def fetch_url(url, timeout=15):
    """GET 并返回 bytes；任何网络/HTTP 失败返回 None（spec §6：单资产失败标 ?）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cpm"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def run_cmd(argv, cwd=None, timeout=60):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def load_json(path):
    path = Path(path)
    if not path.exists():
        raise Broken(f"状态文件缺失: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise Broken(f"状态文件 JSON 损坏: {path}: {e}")


@dataclass
class Asset:
    kind: str                      # plugin | skill | mcp
    name: str
    current: str = ""
    expected: str | None = None    # 应有/最新版本；None = 未检测或检测失败
    source: str | None = None
    managed: bool = True
    enabled: bool | None = None    # 仅 plugin
    notes: str = ""
    plan: dict | None = None       # {"type": "claude-plugin"|"git"|"zip"|"print", ...}
    dir: str | None = None         # 仅 skill：安装目录


class Ctx:
    """所有外部路径的单一来源。真实运行用默认值；测试传伪 HOME。"""

    def __init__(self, home=None, node_modules=None, cpm_json=None):
        self.home = Path(home or Path.home())
        self.claude = self.home / ".claude"
        self.plugins_json = self.claude / "plugins/installed_plugins.json"
        self.marketplaces = self.claude / "plugins/marketplaces"
        self.settings = self.claude / "settings.json"
        self.skills = self.claude / "skills"
        self.claude_json = self.home / ".claude.json"
        self.node_modules = Path(node_modules or "/usr/lib/node_modules")
        self.cpm_json = Path(cpm_json or Path(__file__).resolve().parent / "cpm.json")


# ---- 三类适配器与命令实现随任务 2-9 逐步填充 ----

def load_assets(ctx):
    return load_plugins(ctx) + load_skills(ctx) + load_mcps(ctx)


def load_plugins(ctx):
    inst = load_json(ctx.plugins_json).get("plugins", {})
    enabled = {}
    if ctx.settings.exists():
        enabled = load_json(ctx.settings).get("enabledPlugins", {})
    res = []
    for pid, recs in inst.items():
        name, mp = pid.split("@", 1)
        rec = recs[0]  # 本机全部 user scope；如出现多 scope 记录，取第一条
        ver = rec.get("version") or (rec.get("gitCommitSha") or "?")[:12]
        on = enabled.get(pid, True)
        res.append(Asset(
            kind="plugin", name=name, current=ver,
            source=f"marketplace:{mp}", enabled=on,
            notes="" if on else "已禁用",
            plan={"type": "claude-plugin", "id": pid, "marketplace": mp,
                  "installPath": rec.get("installPath", "")},
        ))
    return res


def load_skills(ctx):
    return []          # 任务 5 实现


def load_mcps(ctx):
    return []          # 任务 8 实现


def outdated(current, expected):
    return None  # 任务 3 实现


def row_status(a, checked):
    if not a.managed:
        return "不管理"
    if not checked:
        return "待检测"
    if a.expected is None:
        return "?"
    return "可更新" if outdated(a.current, a.expected) else "最新"


def render(assets, as_json, checked):
    if as_json:
        return json.dumps([{**asdict(a), "status": row_status(a, checked)} for a in assets],
                          ensure_ascii=False, indent=2)
    lines = []
    for a in assets:
        tgt = f"{a.current} -> {a.expected or '?'}" if checked else (a.current or "?")
        note = f"  | {a.notes}" if a.notes else ""
        lines.append(f"{a.kind:<6} {a.name:<24} {tgt:<30} {row_status(a, checked)}{note}")
    return "\n".join(lines)


def cmd_status(ctx):
    print(render(load_assets(ctx), False, checked=False))
    return EXIT_OK


def cmd_check_update(ctx, as_json=False):
    raise NotImplementedError  # 任务 4


def cmd_update(ctx, names, all_, dry_run):
    raise NotImplementedError  # 任务 9


def cmd_add(ctx, skill, url):
    raise NotImplementedError  # 任务 9


def main(argv=None, ctx=None):
    ap = argparse.ArgumentParser(prog="cpm", description="Claude Code 资产管理（plugin/skill/MCP）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="盘点三类资产（离线）")
    p = sub.add_parser("check-update", help="刷新 marketplace 并联网比对可更新项")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    p = sub.add_parser("update", help="执行更新")
    p.add_argument("names", nargs="*", help="资产名（与 --all 二选一）")
    p.add_argument("--all", action="store_true", help="更新全部可更新项")
    p.add_argument("--dry-run", action="store_true", help="只显示将做什么")
    p = sub.add_parser("add", help="补录 skill 来源到 cpm.json")
    p.add_argument("skill")
    p.add_argument("url")
    args = ap.parse_args(argv)
    ctx = ctx or Ctx()
    try:
        if args.cmd == "status":
            return cmd_status(ctx)
        if args.cmd == "check-update":
            return cmd_check_update(ctx, args.json)
        if args.cmd == "update":
            return cmd_update(ctx, args.names, args.all, args.dry_run)
        if args.cmd == "add":
            return cmd_add(ctx, args.skill, args.url)
    except Broken as e:
        print(f"cpm: {e}", file=sys.stderr)
        return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
