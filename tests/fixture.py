import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpm

MARKET = "claude-plugins-official"


def sh(argv, cwd):
    subprocess.run(argv, cwd=str(cwd), check=True, capture_output=True)


GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def build_home(root: Path) -> Path:
    """构建伪 HOME。返回 root。结构对应真实环境：

    .claude/plugins/installed_plugins.json   3 个插件 aa(path型)/bb(url型)/cc(semver型)
    .claude/plugins/marketplaces/claude-plugins-official/   真 git 仓库，aa 有两个提交
    .claude/plugins/cache/aa/               含 package.json + .mcp.json(npx) 的缓存目录
    .claude/settings.json                   enabledPlugins: aa 启用 / cc 禁用
    .claude/skills/anysearch/               README 声明来源型（无 .git）
    .claude/skills/gitskill/                有 .git + github remote 型
    .claude/skills/orphan/                  无线索型
    .claude.json                            全局 mcp codegraph/context-mode + 项目级 playwright
    node_modules/                           假全局 npm 树（两个包的 package.json）
    """
    root = Path(root)
    cl = root / ".claude"
    mp = cl / "plugins/marketplaces" / MARKET
    (mp / ".claude-plugin").mkdir(parents=True)
    (mp / "plugins/aa").mkdir(parents=True)
    (mp / "plugins/aa/SKILL.md").write_text("aa v1\n")
    entries = [
        {"name": "aa", "source": "./plugins/aa",
         "homepage": "https://github.com/test-org/aa-plugin"},
        {"name": "bb", "source": {"source": "url",
                                  "url": "https://github.com/x/bb.git",
                                  "sha": "deadbeef00000000000000000000000000000000"}},
        {"name": "cc", "version": "2.0.0", "source": "./plugins/cc"},
    ]
    (mp / ".claude-plugin/marketplace.json").write_text(json.dumps({"plugins": entries}))
    sh(["git", "init", "-q"], mp)
    sh(["git", "add", "-A"], mp)
    sh(["git", *GIT_ID, "commit", "-q", "-m", "r1"], mp)
    sha_r1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(mp),
                            capture_output=True, text=True).stdout.strip()
    # 推进 aa，形成“marketplace 已前进、已装落后”的局面
    (mp / "plugins/aa/SKILL.md").write_text("aa v2\n")
    sh(["git", "add", "-A"], mp)
    sh(["git", *GIT_ID, "commit", "-q", "-m", "r2"], mp)

    cache = cl / "plugins/cache/aa"
    cache.mkdir(parents=True)
    (cache / "package.json").write_text(json.dumps({"name": "aa-pkg", "version": "0.1.0"}))
    (cache / ".mcp.json").write_text(json.dumps({"srv": {"command": "npx", "args": ["p@latest"]}}))

    inst = {"version": 2, "plugins": {
        f"aa@{MARKET}": [{"scope": "user", "installPath": str(cache),
                          "version": sha_r1[:12], "gitCommitSha": sha_r1}],
        f"bb@{MARKET}": [{"scope": "user", "installPath": str(cl / "plugins/cache/bb"),
                          "version": "cafebabe0000", "gitCommitSha": "cafebabe0000000000000000000000000000abcd"}],
        f"cc@{MARKET}": [{"scope": "user", "installPath": str(cl / "plugins/cache/cc"),
                          "version": "1.0.0", "gitCommitSha": "abc123"}],
    }}
    (cl / "plugins").mkdir(parents=True, exist_ok=True)
    (cl / "plugins/installed_plugins.json").write_text(json.dumps(inst))
    (cl / "plugins/known_marketplaces.json").write_text(json.dumps({
        MARKET: {"source": {"source": "github", "repo": f"test-org/{MARKET}"},
                 "installLocation": str(mp), "lastUpdated": "x"}}))
    (cl / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {f"aa@{MARKET}": True, f"cc@{MARKET}": False}}))

    sk = cl / "skills"
    a = sk / "anysearch"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text("---\nname: anysearch\nversion: 3.1.1\n---\n\n# AnySearch\n")
    (a / "README.md").write_text("# AnySearch\n\nDownload & install: "
                                 "https://github.com/anysearch-ai/anysearch-skill/releases\n")
    (a / ".gitignore").write_text("runtime.conf\n.env\n")
    (a / "runtime.conf").write_text("python3 /opt/cli.py\n")
    g = sk / "gitskill"
    g.mkdir()
    sh(["git", "init", "-q"], g)
    (g / "SKILL.md").write_text("---\nname: gitskill\nversion: 0.1.0\n---\n")
    sh(["git", "add", "-A"], g)
    sh(["git", *GIT_ID, "commit", "-q", "-m", "i1"], g)
    cfg = g / ".git/config"
    cfg.write_text(cfg.read_text() + '[remote "origin"]\n\turl = https://github.com/foo/gitskill.git\n')
    o = sk / "orphan"
    o.mkdir()
    (o / "SKILL.md").write_text("---\nname: orphan\nversion: 1.0.0\n---\n")

    claude_json = {
        "mcpServers": {
            "codegraph": {"type": "stdio", "command": "codegraph", "args": ["serve", "--mcp"]},
            "context-mode": {"type": "stdio", "command": "context-mode", "args": []},
        },
        "projects": {"/home/x/proj": {"mcpServers": {
            "playwright": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"]}}}},
    }
    (root / ".claude.json").write_text(json.dumps(claude_json))

    nm = root / "node_modules"
    cg = nm / "@colbymchenry/codegraph"
    cg.mkdir(parents=True)
    (cg / "package.json").write_text(json.dumps(
        {"name": "@colbymchenry/codegraph", "version": "0.9.9", "bin": {"codegraph": "npm-shim.js"}}))
    cm = nm / "context-mode"
    cm.mkdir()
    (cm / "package.json").write_text(json.dumps(
        {"name": "context-mode", "version": "1.0.162", "bin": {"context-mode": "cli.bundle.mjs"}}))
    return root


def test_ctx(root: Path) -> cpm.Ctx:
    """伪 HOME 对应的 Ctx；cpm.json 也放进临时目录，绝不污染仓库。"""
    return cpm.Ctx(root, node_modules=root / "node_modules",
                   cpm_json=root / "cpm.json")


def zip_bytes(prefix: str, files: dict[str, bytes]) -> bytes:
    """构造 release 源码包形态的 zip：顶层目录 prefix/ + 内附 SHA256SUMS.txt。"""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        sums = []
        for rel, data in files.items():
            z.writestr(f"{prefix}/{rel}", data)
            sums.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
        z.writestr(f"{prefix}/SHA256SUMS.txt", "\n".join(sums) + "\n")
    return buf.getvalue()


def patch_fetch(test, mapping):
    """mapping: URL 子串 -> bytes|dict。未命中返回 None（模拟网络失败）。"""
    real = cpm.fetch_url

    def fake(url, timeout=15):
        for k, v in mapping.items():
            if k in url:
                return v if isinstance(v, bytes) else json.dumps(v).encode()
        return None

    cpm.fetch_url = fake
    test.addCleanup(setattr, cpm, "fetch_url", real)


def patch_run(test, handlers):
    """handlers: 命令前缀(tuple) -> callable(argv, cwd) -> (rc,out,err)|None。
    返回 None 表示放行给真实 run_cmd（例：同是 git，stub 掉 pull 但放行 log，
    保证 marketplace 比对仍走真实仓库）。未命中的默认走真实。"""
    real = cpm.run_cmd

    def fake(argv, cwd=None, timeout=60):
        for pref, fn in handlers.items():
            if tuple(argv[:len(pref)]) == tuple(pref):
                r = fn(argv, cwd)
                if r is not None:
                    return r
        return real(argv, cwd, timeout)

    cpm.run_cmd = fake
    test.addCleanup(setattr, cpm, "run_cmd", real)
