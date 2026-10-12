#!/usr/bin/env python3
"""cpm — 盘点 Claude Code 的 plugin / skill / MCP 资产，检查并执行更新。

设计见 docs/superpowers/specs/2026-10-10-cpm-design.md。
网络(fetch_url)与子进程(run_cmd)收敛为模块级函数，测试注入替换后可全离线运行。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

EXIT_OK, EXIT_OUTDATED, EXIT_PARTIAL, EXIT_BROKEN = 0, 1, 2, 3
REFRESH_TIMEOUT = 300  # marketplace 刷新含网络 git（真机实测可超 60s），单独放宽

if sys.platform == "win32":
    # Windows 重定向（管道/文件）时 stdout/stderr 默认按 GBK：✓ 等符号直接抛
    # UnicodeEncodeError，--json 中文输出也不是 UTF-8。真终端走 ConsoleIO 不受影响。
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")


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
    # Windows 上 npm/claude 是 .cmd shim，CreateProcess 不搜裸名（仅 .exe），
    # 须经 shutil.which 解析出真实路径；Linux 下 which 命中同一目标，行为不变。
    argv = list(argv)
    disp = " ".join(map(str, argv))
    resolved = shutil.which(str(argv[0]))
    if resolved:
        argv[0] = resolved
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"子进程超时({timeout}s): {disp}"
    return p.returncode, p.stdout, p.stderr


_allow_scripts_probe: bool | None = None


def npm_has_allow_scripts():
    """能力探测（一次缓存）：npm v12 系默认跳过 install scripts，须 --allow-scripts 放行；
    旧 npm（真机 11.12.1）不识别该 flag，硬带会报 Unknown cli config 警告。
    依据：npm config ls -l 是否有 allow-scripts 配置项。npm 缺失/失败按 False。"""
    global _allow_scripts_probe
    if _allow_scripts_probe is None:
        rc, out, _ = run_cmd(["npm", "config", "ls", "-l"])
        _allow_scripts_probe = rc == 0 and bool(
            re.search(r"^allow-scripts\s*=", out or "", re.M))
    return _allow_scripts_probe


def npm_global_install_cmd(pkg, spec=None):
    """给用户打印的全局更新命令：sudo 仅 Linux/macOS 有意义，Windows 无 sudo；
    --allow-scripts 仅新 npm 支持时才加（见 npm_has_allow_scripts）。"""
    prefix = "" if os.name == "nt" else "sudo "
    flag = f"--allow-scripts={pkg} " if npm_has_allow_scripts() else ""
    target = f"{pkg}@{spec}" if spec else f"{pkg}@<latest>"
    return f"{prefix}npm i -g {flag}{target}"


def _rmtree_force(path):
    """跨平台强删：git 对象在 Windows 是只读文件，须先解除只读再重试。
    （换装备份目录可能含 .git；静默跳过会在 skills/ 留下垃圾目录）"""
    def _onerr(func, p, exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onerr)
    else:
        shutil.rmtree(path, onerror=lambda func, p, ei: _onerr(func, p, None))


def default_node_modules():
    """全局 npm 树默认位置：平台候选优先，都落空再问 `npm root -g`
    （nvm/自定义 prefix 等非常规装法），仍失败返回首个候选（find_npm_pkg 容忍缺失）。"""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        cands = [Path(appdata) / "npm" / "node_modules"] if appdata else []
    else:
        cands = [Path("/usr/lib/node_modules"), Path("/usr/local/lib/node_modules")]
    for c in cands:
        if c.is_dir():
            return c
    rc, out, _ = run_cmd(["npm", "root", "-g"])
    if rc == 0 and out.strip() and Path(out.strip()).is_dir():
        return Path(out.strip())
    return cands[0] if cands else Path("/usr/lib/node_modules")


def load_json(path):
    path = Path(path)
    if not path.exists():
        raise Broken(f"状态文件缺失: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
    commit: str | None = None      # 仅 plugin：账本 gitCommitSha（比对的真实身份）


class Ctx:
    """所有外部路径的单一来源。真实运行用默认值；测试传伪 HOME。"""

    def __init__(self, home=None, node_modules=None, cpm_json=None):
        self.home = Path(home or Path.home())
        self.claude = self.home / ".claude"
        self.plugins_json = self.claude / "plugins/installed_plugins.json"
        self.known_marketplaces = self.claude / "plugins/known_marketplaces.json"
        self.marketplaces = self.claude / "plugins/marketplaces"
        self.settings = self.claude / "settings.json"
        self.skills = self.claude / "skills"
        self.cpm_trash = self.claude / ".cpm_trash"
        self.claude_json = self.home / ".claude.json"
        self.node_modules = Path(node_modules) if node_modules else default_node_modules()
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
        # enabledPlugins 是白名单：无条目=禁用（真机实证，与 claude plugin list 一致）
        on = bool(enabled.get(pid, False))
        res.append(Asset(
            kind="plugin", name=name, current=ver,
            commit=rec.get("gitCommitSha"),
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
        m = GH_REPO.search(cfg.read_text(encoding="utf-8", errors="ignore"))
        if m:
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}", ".git/config"
    repos = set()
    for fn in ("README.md", "SKILL.md"):
        p = d / fn
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
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
        m = re.search(r"^version:\s*([\w.\-]+)", sk.read_text(encoding="utf-8", errors="ignore")[:4000], re.M)
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


def find_npm_pkg(nm, cmd):
    """全局 npm 树里按 bin 名反查包（spec §4.3：~/.claude.json 记的是 bin 名）。"""
    nm = Path(nm)
    if not nm.exists():
        return None, None
    dirs = [p for p in nm.iterdir() if p.is_dir()]
    for p in [q for q in dirs if q.name.startswith("@")]:
        dirs.extend(r for r in p.iterdir() if r.is_dir())
    for p in dirs:
        pj = p / "package.json"
        if not pj.exists():
            continue
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        bins = d.get("bin")
        if isinstance(bins, str):
            bins = {d.get("name", ""): bins}
        if isinstance(bins, dict) and cmd in bins:
            return d.get("name"), p
        if d.get("name") == cmd:
            return d.get("name"), p
    return None, None


def load_mcps(ctx):
    cfg = load_json(ctx.claude_json)
    res = []
    for name, srv in cfg.get("mcpServers", {}).items():
        cmd = srv.get("command", "")
        pkg, root = find_npm_pkg(ctx.node_modules, cmd)
        if pkg:
            ver = json.loads((root / "package.json").read_text(encoding="utf-8")).get("version", "?")
            res.append(Asset(kind="mcp", name=name, current=ver,
                             source=f"npm:{pkg}",
                             plan={"type": "print", "pkg": pkg,
                                   "cmd": npm_global_install_cmd(pkg)}))
        else:
            res.append(Asset(kind="mcp", name=name,
                             current=srv.get("type", "stdio"),
                             managed=False, notes="非全局 npm 装法或包未找到，来源未知"))
    for proj, pv in cfg.get("projects", {}).items():
        for name in pv.get("mcpServers", {}):
            res.append(Asset(kind="mcp", name=f"{name}@{proj}", current="(项目级)",
                             managed=False, notes="项目级配置；与官方插件重叠，cpm 不处理"))
    return res


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
        # 路径型：clone 内 plugin.json 的 version 优先（官方 up_to_date 的真实基准，
        # 真机实证：code-simplifier 账本 1.0.0 == clone 1.0.0 即最新，path commit 再新也不更新）
        pj = Path(mp_dir) / src[2:] / ".claude-plugin" / "plugin.json"
        if pj.exists():
            try:
                ver = json.loads(pj.read_text(encoding="utf-8")).get("version")
            except json.JSONDecodeError:
                ver = None
            if ver:
                return ver, f"clone plugin.json（{src}）"
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


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def cmp_current(a):
    """plugin 的真实身份是账本 gitCommitSha：expected 为 sha 形态时以 commit 为准参与比对。"""
    if (a.kind == "plugin" and a.commit
            and a.expected and _SHA_RE.match(a.expected)):
        return a.commit[:12]
    return a.current


def display_current(a):
    """展示层：sha 比对形态下用 `语义版本/短sha` 双标识（用户要求）；
    version 本身已是 sha 前缀（路径型账本形态）时不重复显示。"""
    if (a.kind == "plugin" and a.commit and a.expected
            and _SHA_RE.match(a.expected) and not a.commit.startswith(a.current)):
        return f"{a.current}/{a.commit[:12]}"
    return a.current


def is_outdated(a):
    return outdated(cmp_current(a), a.expected)


def row_status(a, checked):
    if not a.managed:
        return "不管理"
    if not checked:
        return "待检测"
    if a.expected is None:
        return "?"
    return "可更新" if is_outdated(a) else "最新"


def render(assets, as_json, checked):
    if as_json:
        return json.dumps([{**asdict(a), "status": row_status(a, checked)} for a in assets],
                          ensure_ascii=False, indent=2)
    lines = []
    for a in assets:
        # 不管理的行没有"应有版本"可比，不显示箭头；notes 避免产生空的双竖线
        cur = display_current(a) if (checked and a.managed) else a.current
        tgt = f"{cur} -> {a.expected or '?'}" if (checked and a.managed) else (cur or "?")
        note = f"  | {a.notes.lstrip(' |')}" if a.notes else ""
        lines.append(f"{a.kind:<6} {a.name:<24} {tgt:<30} {row_status(a, checked)}{note}")
    return "\n".join(lines)


def cmd_status(ctx):
    print(render(load_assets(ctx), False, checked=False))
    return EXIT_OK


def marketplace_slug(ctx, mp_name):
    """marketplace 的 GitHub owner/repo（known_marketplaces.json 的 source 声明）。"""
    if not ctx.known_marketplaces.exists():
        return None
    src = load_json(ctx.known_marketplaces).get(mp_name, {}).get("source", {})
    if src.get("source") == "github" and src.get("repo"):
        return src["repo"]
    m = GH_REPO.search(src.get("url", "")) if src.get("url") else None
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}" if m else None


def declared_repo(entry):
    """marketplace 条目声明的上游仓库 owner/repo（url 型取 source.url，路径型取 homepage）。"""
    src = entry.get("source")
    url = src.get("url") if isinstance(src, dict) else None
    url = url or entry.get("homepage")
    m = GH_REPO.search(url) if url else None
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}" if m else None


def _npm_view_fields(argv_pkg):
    """npm view <pkg> version repository.url 的 key = 'value' 行解析。"""
    rc, out, _ = run_cmd(["npm", "view", argv_pkg, "version", "repository.url"])
    if rc != 0:
        return {}
    fields = {}
    for line in out.splitlines():
        k, sep, v = line.partition(" = ")
        if sep:
            fields[k.strip()] = v.strip().strip("'\"")
    return fields


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
        if a.expected is None and isinstance(entry.get("source"), str) \
                and entry["source"].startswith("./"):
            # 真机形态：官方 update 后 clone 无 .git。对齐官方 up_to_date 判定：
            # ① clone 里该插件 plugin.json 的 version（semver）优先；
            # ② 没有 version 才查远端 path 最后提交（GitHub ?path= 会把未触碰该路径的
            #    merge commit 也算进来——真机 code-simplifier 假阳性教训——须跳过 parents>1）
            pj = mp_dir / entry["source"][2:] / ".claude-plugin" / "plugin.json"
            ver = None
            if pj.exists():
                try:
                    ver = json.loads(pj.read_text(encoding="utf-8")).get("version")
                except json.JSONDecodeError:
                    pass
            if ver:
                a.expected = ver
                a.source = f"clone plugin.json（{entry['source']}）"
            else:
                slug = marketplace_slug(ctx, a.plan["marketplace"])
                if slug:
                    b = fetch_url(f"https://api.github.com/repos/{slug}/commits"
                                  f"?path={entry['source'][2:]}&per_page=20")
                    try:
                        js = next((c["sha"] for c in json.loads(b)
                                   if len(c.get("parents", [1])) == 1), None) if b else None
                    except Exception:
                        js = None
                    if js:
                        a.expected = js
                        a.source = f"marketplace 远端 path 提交 {slug}"
                        a.notes += " | 本地无 .git，改用远端 path 提交对比"
        ip = Path(a.plan["installPath"]) if a.plan.get("installPath") else None
        if ip and (ip / "package.json").exists():            # 辅助检测：npm 发布通道（需身份校验）
            pkg = json.loads((ip / "package.json").read_text(encoding="utf-8")).get("name")
            f = _npm_view_fields(pkg)
            npm_repo = f.get("repository.url")
            m = GH_REPO.search(npm_repo) if npm_repo else None
            want = declared_repo(entry)
            # 真机教训：npm 同名包可能是无关占位包（superpowers=0.0.2），身份不符则不标注
            if f.get("version") and m and want and f"{m.group(1)}/{m.group(2).removesuffix('.git')}".lower() == want.lower():
                a.notes += f" | npm 通道 {f['version']}"
        if ip and (ip / ".mcp.json").exists():               # npx 运行时插件自标注
            try:
                d = json.loads((ip / ".mcp.json").read_text(encoding="utf-8"))
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


def gh_head(repo):
    """默认分支 HEAD 的 commit sha（API）。本环境 github.com:443 不可达、
    api.github.com 可达，分支对比必须走 API 而非 ls-remote（真机实证）。"""
    b = fetch_url(f"https://api.github.com/repos/{repo}/commits/HEAD")
    if b:
        try:
            return json.loads(b).get("sha")
        except Exception:
            pass
    return None


def check_skills(ctx, assets):
    for a in assets:
        if a.kind != "skill" or not a.managed or not a.source:
            continue
        a.expected = gh_latest(a.source)
        if a.expected is None and a.plan and a.plan.get("type") == "git":
            # 上游无 release 无 tag（纯分支发布，真机案例 op7418/Humanizer-zh）：对比默认分支 HEAD
            a.expected = gh_head(a.source)
            if a.expected is None:
                a.notes += " 无 release/tag，API 也取不到分支头"


def check_mcps(ctx, assets):
    for a in assets:
        if a.kind == "mcp" and a.plan and a.plan.get("type") == "print":
            rc, out, _ = run_cmd(["npm", "view", a.plan["pkg"], "version"])
            if rc == 0 and out.strip():
                a.expected = out.strip()
                a.plan["cmd"] = npm_global_install_cmd(a.plan["pkg"], out.strip())


def cmd_check_update(ctx, as_json=False):
    # 进度一律走 stderr：stdout 保持纯净（--json 可直接被管道消费）
    t0 = time.time()
    say = lambda m: print(m, file=sys.stderr, flush=True)
    assets = load_assets(ctx)
    n = {"plugin": 0, "skill": 0, "mcp": 0}
    for a in assets:
        n[a.kind] += 1
    say(f"cpm: 共 {len(assets)} 项（plugin {n['plugin']} / skill {n['skill']} / mcp {n['mcp']}）")
    say("cpm: 刷新 marketplace（网络 git，最长等 5 分钟）…")
    rc, _, err = run_cmd(["claude", "plugin", "marketplace", "update"], timeout=REFRESH_TIMEOUT)
    refresh_ok = rc == 0
    say(f"cpm: marketplace 刷新 {'成功' if refresh_ok else '失败'}（{time.time() - t0:.1f}s）")
    if not refresh_ok:
        say(f"cpm: 比对结果可能偏旧: {err.strip()}")
    say("cpm: plugin 本地比对…")
    check_plugins(ctx, assets)
    say("cpm: skill 联网检查（GitHub API）…")
    check_skills(ctx, assets)
    say("cpm: mcp 检查（npm view）…")
    check_mcps(ctx, assets)
    upd = sum(1 for a in assets if is_outdated(a))
    say(f"cpm: 完成，用时 {time.time() - t0:.1f}s，{upd} 项可更新")
    print(render(assets, as_json, checked=True))
    failed = (not refresh_ok) or any(a.expected is None for a in assets if a.managed)
    upd = any(is_outdated(a) for a in assets)
    return EXIT_PARTIAL if failed else (EXIT_OUTDATED if upd else EXIT_OK)


def update_skill_zip(a, dry_run, out=print):
    """release zip 换装（spec §4.2）：下载→SHA256SUMS 校验→保留 .gitignore 本地文件→原子交换。
    校验不通过绝不触碰现装目录。"""
    if not a.expected:
        return False, "缺少目标版本（先 check-update）"
    ref = a.expected
    url = f"https://github.com/{a.source}/archive/refs/tags/{ref}.zip"
    if dry_run:
        out(f"  将执行: 下载 {url} → SHA256SUMS 校验 → 保留本地 .gitignore 文件 → 换装 {a.dir}")
        return True, "dry-run"
    blob = fetch_url(url)
    if blob is None:
        return False, f"下载失败: {url}"
    old = Path(a.dir)
    # 解包目录与目标同盘（skills/ 下），否则跨盘 rename 在 Linux 抛 EXDEV、
    # 在 Windows 直接失败（temp 在 C:、目标在 D: 很常见）。
    with tempfile.TemporaryDirectory(dir=str(old.parent)) as td:
        zpath = Path(td) / "d.zip"
        zpath.write_bytes(blob)
        ex = Path(td) / "ex"
        ex.mkdir()
        try:
            with zipfile.ZipFile(zpath) as z:
                z.extractall(ex)
        except zipfile.BadZipFile:
            return False, "zip 损坏"
        inner = next(iter(ex.iterdir()))          # 顶层 <repo>-<tag>/ 目录
        sums = inner / "SHA256SUMS.txt"
        if sums.exists():
            for line in sums.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                want, rel = parts[0].strip(), parts[1].strip().lstrip("./")
                f = inner / rel
                if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != want:
                    return False, f"SHA256 校验失败: {rel}"
        # 保留现装目录里 .gitignore 声明的本地生成文件（runtime.conf/.env 等）
        keep = []
        gi = old / ".gitignore"
        if gi.exists():
            keep = [ln.strip() for ln in gi.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if ln.strip() and not ln.startswith("#") and not ln.startswith("!")]
        for rel in keep:
            src = old / rel
            if src.is_file():
                shutil.copy2(src, inner / rel)
            elif src.is_dir():
                dst = inner / rel
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
        if gi.exists() and not (inner / ".gitignore").exists():
            shutil.copy2(gi, inner / ".gitignore")
        bak = old.with_name(old.name + "__cpm_bak")
        # Windows 上现装目录可能被占用（编辑器/杀软句柄）：挪不开就原样退出
        try:
            old.rename(bak)
        except OSError as e:
            return False, f"现装目录挪开失败（Windows 常见为被占用）: {e}"
        try:
            inner.rename(old)
        except OSError as e:
            try:
                bak.rename(old)  # 回滚，绝不让资产消失
            except OSError:
                return False, f"换装失败且回滚也失败，现场: {old} 备份在 {bak}: {e}"
            return False, f"换装失败（原目录已回滚，保留备份）: {e}"
        _rmtree_force(bak)
    return True, f"已换装 -> {ref}（保留本地文件 {len(keep)} 项）"


def update_asset(ctx, a, dry_run, out=print):
    p = a.plan or {}
    t = p.get("type")
    if not a.managed or t is None:
        return False, "不管理，跳过"
    if t == "claude-plugin":
        argv = ["claude", "plugin", "update", p["id"]]
        if dry_run:
            out(f"  将执行: {' '.join(argv)}")
            return True, "dry-run"
        rc, _, err = run_cmd(argv, timeout=REFRESH_TIMEOUT)
        return rc == 0, err.strip() or "已委托 claude plugin update"
    if t == "print":
        out(f"  手动执行 -> {p['cmd']}")
        return True, "打印命令"
    if t == "git":
        argv = ["git", "-C", a.dir, "pull", "--ff-only"]
        if dry_run:
            out(f"  将执行: {' '.join(argv)}")
            return True, "dry-run"
        rc, o, e = run_cmd(argv)
        msg = (o + e).strip()
        if rc != 0:
            msg += "（本地有改动或分叉；cpm 不强 pull，请手工处理）"
        return rc == 0, msg or "已 pull"
    if t == "zip":
        return update_skill_zip(a, dry_run, out)
    return False, f"未知 plan 类型 {t}"


def cmd_update(ctx, names, all_, dry_run):
    if not names and not all_:
        print("cpm update: 请给出资产名或使用 --all", file=sys.stderr)
        return EXIT_BROKEN
    # 进度走 stderr，stdout 只留逐项结果与汇总（同 check-update 的约定）
    t0 = time.time()
    say = lambda m: print(m, file=sys.stderr, flush=True)
    assets = load_assets(ctx)
    say("cpm: 刷新 marketplace（网络 git，GitHub 慢时可能要几分钟）…")
    rc, _, err = run_cmd(["claude", "plugin", "marketplace", "update"], timeout=REFRESH_TIMEOUT)
    say(f"cpm: marketplace 刷新 {'成功' if rc == 0 else '失败'}（{time.time() - t0:.1f}s）")
    if rc != 0:
        say(f"cpm: 刷新失败不致命，后续更新委托官方命令自行报错: {err.strip()}")
    say("cpm: 检查更新（本地比对 + 联网）…")
    check_plugins(ctx, assets)
    check_skills(ctx, assets)
    check_mcps(ctx, assets)
    if all_:
        targets = [a for a in assets if is_outdated(a)]
        say(f"cpm: 待更新 {len(targets)} 项")
    else:
        want = set(names)
        targets = [a for a in assets if a.name in want or (a.plan or {}).get("id") in want]
        for m in sorted(want - {a.name for a in targets}):
            print(f"  未找到资产: {m}", file=sys.stderr)
    ok, failed = 0, 0
    for a in targets:
        say(f"cpm: 正在更新 {a.kind} {a.name}…")
        good, msg = update_asset(ctx, a, dry_run)
        print(f"{a.kind}  {a.name}: {'✓' if good else '✗'} {msg}")
        ok, failed = ok + (1 if good else 0), failed + (0 if good else 1)
    print(f"汇总: 成功 {ok} / 失败 {failed}" + ("（dry-run）" if dry_run else ""))
    say(f"cpm: 完成，用时 {time.time() - t0:.1f}s")
    return EXIT_OK if failed == 0 else EXIT_PARTIAL


def match_asset(a, want):
    """名称匹配规则（update/uninstall 共用）：资产名或 plugin@marketplace id 皆可。"""
    return a.name in want or (a.plan or {}).get("id") in want


def trash_new_path(base, ts, name):
    """回收条目路径 <ts>-<name>；同秒重名追加 -2/-3…，绝不覆盖既有回收内容。"""
    p = base / f"{ts}-{name}"
    i = 2
    while p.exists():
        p = base / f"{ts}-{name}-{i}"
        i += 1
    return p


def uninstall_asset(ctx, a, dry_run):
    p = a.plan or {}
    if a.kind == "plugin":
        argv = ["claude", "plugin", "uninstall", p["id"]]
        if dry_run:
            return True, f"将执行: {' '.join(argv)}"
        rc, _, err = run_cmd(argv)
        return rc == 0, err.strip() or "已委托 claude plugin uninstall"
    if a.kind == "skill":
        d = Path(a.dir)
        if dry_run:
            return True, f"将移入回收站: {d}"
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = trash_new_path(ctx.cpm_trash, ts, a.name)
        ctx.cpm_trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), dest)
        (dest / ".cpm_removed.json").write_text(json.dumps(
            {"kind": "skill", "name": a.name, "removed": ts}, ensure_ascii=False))
        return True, f"已移入回收站 {dest.name}"
    if a.kind == "mcp":
        if p.get("type") != "print" or not p.get("pkg"):
            return False, "非全局 npm 装法，cpm 不处理"
        if dry_run:
            return True, f"将移除 mcpServers.{a.name} 并存档；将执行: sudo npm rm -g {p['pkg']}"
        cfg = load_json(ctx.claude_json)
        srv = cfg.get("mcpServers", {}).pop(a.name)
        ts = time.strftime("%Y%m%d-%H%M%S")
        ctx.cpm_trash.mkdir(parents=True, exist_ok=True)
        path = trash_new_path(ctx.cpm_trash, ts, a.name + ".json")
        path.write_text(json.dumps(
            {"kind": "mcp", "name": a.name, "removed": ts, "pkg": p["pkg"],
             "version": a.current, "server": srv}, ensure_ascii=False, indent=2))
        ctx.claude_json.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        return True, f"已移除配置并存档 {path.name}；手动执行 -> sudo npm rm -g {p['pkg']}"
    return False, "该类别暂不支持"


def list_trash(ctx):
    """扫描回收站 → [{kind, name, removed, path}]。
    skill 条目 = 目录 + 内嵌 .cpm_removed.json 标记；mcp 条目 = JSON 存档文件。"""
    res = []
    if not ctx.cpm_trash.exists():
        return res
    for p in sorted(ctx.cpm_trash.iterdir()):
        marker = None
        if p.is_file() and p.suffix == ".json":
            marker = p
        elif p.is_dir() and (p / ".cpm_removed.json").exists():
            marker = p / ".cpm_removed.json"
        if marker is None:
            continue
        try:
            d = json.loads(marker.read_text())
        except json.JSONDecodeError:
            continue
        if d.get("kind") in ("skill", "mcp"):
            res.append({**d, "path": str(p)})
    return res


def cmd_trash(ctx):
    entries = list_trash(ctx)
    if not entries:
        print("回收站为空（cpm uninstall 的 skill/MCP 存档会出现在这里）")
        return EXIT_OK
    for e in entries:
        print(f"{e['kind']:<6} {e['name']:<24} 卸载于 {e['removed']}")
    print(f"共 {len(entries)} 条；恢复用 cpm restore <name>")
    return EXIT_OK


def cmd_restore(ctx, name):
    entries = [e for e in list_trash(ctx) if e["name"] == name]
    if not entries:
        print(f"未找到回收记录: {name}（plugin 系官方卸载、无备份，重装用 claude plugin install）",
              file=sys.stderr)
        return EXIT_PARTIAL
    e = entries[-1]      # 同名多条取最新（条目路径带时间戳前缀，排序即时间序）
    p = Path(e["path"])
    if e["kind"] == "skill":
        dest = ctx.skills / name
        if dest.exists():
            print(f"目标已存在，不覆盖: {dest}（回收条目保留在 {p}）", file=sys.stderr)
            return EXIT_PARTIAL
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest)
        (dest / ".cpm_removed.json").unlink(missing_ok=True)
        print(f"✓ skill 已恢复: {dest}")
        return EXIT_OK
    if e["kind"] == "mcp":
        cfg = load_json(ctx.claude_json)
        if name in cfg.get("mcpServers", {}):
            print(f"目标已存在，不覆盖: mcpServers.{name}（回收条目保留）", file=sys.stderr)
            return EXIT_PARTIAL
        cfg.setdefault("mcpServers", {})[name] = e["server"]
        ctx.claude_json.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        p.unlink()
        msg = f"✓ MCP 配置已恢复: {name}"
        if e.get("pkg"):
            ver = f"@{e['version']}" if e.get("version") else ""
            msg += (f"；npm 包未恢复，手动执行 -> "
                    f"sudo npm i -g --allow-scripts={e['pkg']} {e['pkg']}{ver}")
        print(msg)
        return EXIT_OK


def cmd_uninstall(ctx, names, dry_run):
    assets = load_assets(ctx)
    want = set(names)
    targets = [a for a in assets if match_asset(a, want)]
    for m in sorted(want - {a.name for a in targets}):
        print(f"  未找到资产: {m}", file=sys.stderr)
    ok, failed = 0, 0
    for a in targets:
        good, msg = uninstall_asset(ctx, a, dry_run)
        print(f"{a.kind}  {a.name}: {'✓' if good else '✗'} {msg}")
        ok, failed = ok + (1 if good else 0), failed + (0 if good else 1)
    print(f"汇总: 成功 {ok} / 失败 {failed}" + ("（dry-run）" if dry_run else ""))
    return EXIT_OK if failed == 0 else EXIT_PARTIAL


def cmd_add(ctx, skill, url):
    data = load_json(ctx.cpm_json) if ctx.cpm_json.exists() else {"skills": {}}
    data.setdefault("skills", {})[skill] = {"source": url}
    ctx.cpm_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"✓ 已记录 {skill} 来源: {url}（{ctx.cpm_json}）")
    if not (ctx.skills / skill).exists():
        print(f"⚠ {ctx.skills / skill} 目录不存在：将在 skill 安装后生效", file=sys.stderr)
    return EXIT_OK


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
    p = sub.add_parser("uninstall", help="卸载资产（skill 入回收站；MCP 存档配置；plugin 委托官方）")
    p.add_argument("names", nargs="+", help="资产名或 plugin@marketplace id")
    p.add_argument("--dry-run", action="store_true", help="只显示将做什么")
    p = sub.add_parser("trash", help="列出回收站（uninstall 存档的 skill/MCP）")
    p = sub.add_parser("restore", help="从回收站恢复 skill / MCP 配置")
    p.add_argument("name")
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
        if args.cmd == "uninstall":
            return cmd_uninstall(ctx, args.names, args.dry_run)
        if args.cmd == "trash":
            return cmd_trash(ctx)
        if args.cmd == "restore":
            return cmd_restore(ctx, args.name)
        if args.cmd == "add":
            return cmd_add(ctx, args.skill, args.url)
    except Broken as e:
        print(f"cpm: {e}", file=sys.stderr)
        return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
