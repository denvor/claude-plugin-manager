#!/usr/bin/env python3
"""cpm — 盘点 Claude Code 的 plugin / skill / MCP 资产，检查并执行更新。

设计见 docs/superpowers/specs/2026-10-10-cpm-design.md。
网络(fetch_url)与子进程(run_cmd)收敛为模块级函数，测试注入替换后可全离线运行。
"""
import argparse
import json
import re
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


GH_REPO = re.compile(r"github\.com[/:]([\w.\-]+)/([\w.\-]+)")


def discover_skill_source(d, override):
    """来源发现链（spec §4.2）：cpm.json override → .git/config remote → README 安装行。
    返回 (owner/repo 或 None, 说明)。"""
    if override and override.get("source"):
        m = GH_REPO.search(override["source"])
        if m:
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}", "cpm.json"
        return None, f"cpm.json 里的 source 不是 GitHub URL: {override['source']}"
    cfg = d / ".git/config"
    if cfg.exists():
        m = GH_REPO.search(cfg.read_text(errors="ignore"))
        if m:
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}", ".git/config"
    repos = set()
    for fn in ("README.md", "SKILL.md"):
        p = d / fn
        if not p.exists():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            if re.search(r"install|download|release|clone", line, re.I):
                for m in GH_REPO.finditer(line):
                    repos.add(f"{m.group(1)}/{m.group(2).removesuffix('.git')}")
    if len(repos) == 1:
        return repos.pop(), "README pattern"
    if len(repos) > 1:
        return None, f"来源歧义 {sorted(repos)}，用 cpm add 定夺"
    return None, "无线索"


def skill_version(d):
    sk = d / "SKILL.md"
    if sk.exists():
        m = re.search(r"^version:\s*([\w.\-]+)", sk.read_text(errors="ignore")[:4000], re.M)
        if m:
            return m.group(1)
    rc, out, _ = run_cmd(["git", "-C", str(d), "rev-parse", "--short", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else "?"


def load_skills(ctx):
    overrides = {}
    if ctx.cpm_json.exists():
        overrides = load_json(ctx.cpm_json).get("skills", {})
    res = []
    if not ctx.skills.exists():
        return res
    for d in sorted(p for p in ctx.skills.iterdir() if p.is_dir()):
        src, how = discover_skill_source(d, overrides.get(d.name))
        managed = src is not None
        ptype = "git" if (d / ".git").exists() else ("zip" if managed else None)
        res.append(Asset(
            kind="skill", name=d.name, current=skill_version(d),
            source=src, managed=managed, dir=str(d),
            notes="" if managed else f"不管理（{how}；用 cpm add {d.name} <url> 补录）",
            plan={"type": ptype, "dir": str(d)} if ptype else None,
        ))
    return res


def load_mcps(ctx):
    return []          # 任务 8 实现


def find_marketplace_entry(ctx, mp_name, plugin_name):
    mp_dir = ctx.marketplaces / mp_name
    mj = mp_dir / ".claude-plugin" / "marketplace.json"
    if not mj.exists():
        return None, mp_dir
    for e in load_json(mj).get("plugins", []):
        if e.get("name") == plugin_name:
            return e, mp_dir
    return None, mp_dir


def plugin_expected(mp_dir, entry):
    """返回 (应有版本, 来源描述)。优先级: semver 字段 > url 钉 sha > 路径 git log。"""
    if entry.get("version"):
        return entry["version"], f"marketplace semver（{entry['name']}）"
    src = entry.get("source")
    if isinstance(src, dict):
        return src.get("sha"), f"url 钉 {src.get('url', '?')}"
    if isinstance(src, str) and src.startswith("./"):
        rc, out, _ = run_cmd(["git", "-C", str(mp_dir), "log", "-1", "--format=%H", "--", src[2:]])
        if rc == 0 and out.strip():
            return out.strip(), f"marketplace path {src}"
        return None, f"marketplace path {src}（git log 失败）"
    return None, "未知 source 形态"


def outdated(current, expected):
    if not current or not expected:
        return None
    c, e = str(current).lower().strip(), str(expected).lower().strip()
    c = c[1:] if c.startswith("v") else c
    e = e[1:] if e.startswith("v") else e
    if c == e or e.startswith(c) or c.startswith(e):   # sha 短/全长前缀等价
        return False
    return True


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


def check_plugins(ctx, assets):
    for a in assets:
        if a.kind != "plugin":
            continue
        entry, mp_dir = find_marketplace_entry(ctx, a.plan["marketplace"], a.name)
        if entry is None:
            a.expected = None
            a.notes += " | marketplace 无此条目"
            continue
        a.expected, a.source = plugin_expected(mp_dir, entry)
        ip = Path(a.plan["installPath"]) if a.plan.get("installPath") else None
        if ip and (ip / "package.json").exists():            # 辅助检测：npm 发布通道（spec §4.1）
            pkg = json.loads((ip / "package.json").read_text()).get("name")
            rc, out, _ = run_cmd(["npm", "view", pkg, "version"])
            if rc == 0 and out.strip():
                a.notes += f" | npm 通道 {out.strip()}"
        if ip and (ip / ".mcp.json").exists():               # npx 运行时插件自标注
            try:
                d = json.loads((ip / ".mcp.json").read_text())
                if any(isinstance(s, dict) and s.get("command") == "npx" for s in d.values()):
                    a.notes += " | 运行时经 npx 自更新，无需管理"
            except json.JSONDecodeError:
                pass


def gh_latest(repo):
    """最新 release tag；无 release 的仓库退化到 tags 列表首位。失败 None。"""
    b = fetch_url(f"https://api.github.com/repos/{repo}/releases/latest")
    if b:
        try:
            t = json.loads(b)
            if isinstance(t, dict) and t.get("tag_name"):
                return t["tag_name"]
        except Exception:
            pass
    b = fetch_url(f"https://api.github.com/repos/{repo}/tags")
    if b:
        try:
            tags = json.loads(b)
            if tags and isinstance(tags[0], dict):
                return tags[0]["name"]
        except Exception:
            pass
    return None


def check_skills(ctx, assets):
    for a in assets:
        if a.kind == "skill" and a.managed and a.source:
            a.expected = gh_latest(a.source)


def check_mcps(ctx, assets):
    pass   # 任务 8 实现


def cmd_check_update(ctx, as_json=False):
    assets = load_assets(ctx)
    rc, _, err = run_cmd(["claude", "plugin", "marketplace", "update"])
    refresh_ok = rc == 0
    if not refresh_ok:
        print(f"cpm: marketplace 刷新失败，比对结果可能偏旧: {err.strip()}", file=sys.stderr)
    check_plugins(ctx, assets)
    check_skills(ctx, assets)
    check_mcps(ctx, assets)
    print(render(assets, as_json, checked=True))
    failed = (not refresh_ok) or any(a.expected is None for a in assets if a.managed)
    upd = any(outdated(a.current, a.expected) for a in assets if a.expected)
    return EXIT_PARTIAL if failed else (EXIT_OUTDATED if upd else EXIT_OK)


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
