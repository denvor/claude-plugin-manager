# cpm — Claude Code 资产统一管理 CLI 设计

日期：2026-10-10
状态：已与用户逐节确认
位置：`~/work/claude/plugin_manager`

## 1. 背景与目标

Claude Code 的扩展资产分三类，安装与更新机制互不相同，目前没有统一入口：

| 类别 | 现状 | 安装方式 | 更新方式 |
|---|---|---|---|
| plugin | 7 个，均来自官方 marketplace `claude-plugins-official`（github: anthropics/claude-plugins-official），账本 `~/.claude/plugins/installed_plugins.json`，缓存 `~/.claude/plugins/cache/<mp>/<plugin>/<ver>/` | `claude plugin install <name>@<marketplace>` | 官方 `claude plugin update <name>` |
| skill | `~/.claude/skills/` 下 2 个：anysearch v3.1.1（release zip 解包安装、无 .git、README 声明来源 github.com/anysearch-ai/anysearch-skill、自带 SHA256SUMS.txt 校验清单、.gitignore 列出安装时生成的 runtime.conf/.env）；humanizer-zh（git clone，有 .git） | 手工放置 | 无统一手段 |
| MCP | 全局 2 个（codegraph → /usr/lib/node_modules/@colbymchenry/codegraph；context-mode → /usr/lib/node_modules/context-mode，均 stdio，配置在 `~/.claude.json` 的 `mcpServers`）；项目级 3 处 playwright（crypto / my-satellite-project / GridTrading） | 系统级 `sudo npm i -g` | npm 重装 |

目标：一个 CLI 工具 `cpm`，对以上三类资产提供**盘点 + 检查更新 + 执行更新**。

非目标（已明确排除）：install / uninstall / enable / disable / prune / doctor 等生命周期与配置修复操作——这些继续走官方 `claude plugin` CLI 或手工。

## 2. 命令面

```
cpm status                     # 盘点三类资产：版本、来源、启用状态、是否可管理。完全离线
cpm check-update [--json]      # 先 `claude plugin marketplace update` 刷新，再逐类比对可更新项（联网）
cpm update <name> [--dry-run]  # 更新单个资产
cpm update --all [--dry-run]   # 批量更新；单资产失败不中断其余，末尾汇总
cpm add <skill-name> <url>     # 向 cpm.json 补录某 skill 的来源
```

## 3. 架构

**形态**：单文件 Python 3 CLI（`cpm.py`），仅用 stdlib（`json` / `urllib.request` / `zipfile` / `subprocess` / `unittest`），无第三方依赖。

**模块划分**（同一文件内的分区，预期 600–800 行）：

```
load_state()  ── 聚合读取四种状态文件 + cpm.json ──> [Asset]
Asset = { kind, name, current, source, latest, update_plan, managed, notes }
   │
   ├─ plugin_adapter    （只读 + 委托官方 CLI）
   ├─ skill_adapter     （只读 / 下载换装）
   └─ mcp_adapter       （只读 / 只打印命令，永不执行特权）

网络收敛为一个函数 fetch_url(url) -> bytes|None
子进程收敛为一个函数 run_cmd(argv, cwd=None) -> (rc, stdout, stderr)
—— 测试时注入替换这两个函数即可全离线。
```

**数据源**（全部为既有文件，cpm 只读，唯一写的是自己项目内的 `cpm.json`）：

- `~/.claude/plugins/installed_plugins.json`（已装 plugin：version、gitCommitSha、installPath、scope）
- `~/.claude/plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json`（plugin 版本声明）
- `~/.claude/skills/*/`（SKILL.md frontmatter 的 name/version；.git/config；README 中的 GitHub URL；.gitignore）
- `~/.claude.json`（全局 + 项目 mcpServers）
- `/usr/lib/node_modules/<pkg>/package.json`（MCP 现装版本）

## 4. 关键算法

### 4.1 plugin 更新检测：纯本地比对，零自建查询

前提：`check-update` 开头执行 `claude plugin marketplace update`，使 marketplace 本地 git 副本反映最新声明。之后读 `marketplace.json`，按条目 source 声明的三种形态分别推导"应有版本"，与 `installed_plugins.json` 比对：

1. **路径型**（`"source": "./plugins/x"` 或 `"./external_plugins/x"`）：应有版本 = 副本内 `git log -1 --format=%H -- <path>`（最后触碰该插件目录的提交）；与已装 `gitCommitSha` 按前缀比对。
2. **url 型**（`"source": {"source":"url","url":...,"sha":...}`，如 superpowers 钉外部仓库）：应有版本 = 声明的 `sha`；与已装 `gitCommitSha` 比对。
3. **semver 型**（条目带 `"version": "1.0.0"`，如 rust-analyzer-lsp）：直接比版本号。

执行：委托 `claude plugin update <name>`，不复制任何安装逻辑。**明确不做 npm 覆盖 cache**——插件账本以 gitCommitSha 为准，npm 换装会破坏官方 update/enable 的一致性（插件本体不经 npm 安装，npm 上同名的包只是面向 opencode/pi 等生态的发布产物）。

辅助检测（用户确认加入）：
- cache 目录含 `package.json` 的插件（如 superpowers），额外执行 `npm view <name> version`；npm 版本高于已装时标注 `npm 通道有 X.Y.Z`，仅作参考提示，执行仍走 `claude plugin update`。
- 运行时经 `npx <pkg>@latest` 的插件（如 playwright 的 `.mcp.json`），status 行标注"运行时自更新，无需管理"。

展示：`status` 中列出启用/禁用状态（对照 `~/.claude/settings.json` 的 `enabledPlugins`），禁用插件照常可更新。

### 4.2 skill 来源发现：自动优先，清单补位

优先级链：`cpm.json` 显式条目 → skill 目录 `.git/config` 的 remote → 解析 README.md/SKILL.md 中 `github.com/<owner>/<repo>` 模式。README 解析的确定性规则：只看匹配 `/install|download|release|clone/i` 的行，取其中第一个 repo URL；若这些行中出现 ≥2 个不同的 `<owner>/<repo>` → 判为歧义，status 标注并要求 `cpm add` 定夺。发现失败 → 列为 `⚠ unmanaged`，check-update 跳过。

当前版本：SKILL.md frontmatter `version:` 字段（无则视为与 source 绑定，不做 semver 比较，只比 ref/commit）。
最新版本三级链（执行期真机修正，原两级不够）：GitHub API `releases/latest` → `tags` 首位 → **git 型 skill 再退化 `commits/HEAD`（默认分支 sha）**。纯分支发布上游真实存在（op7418/Humanizer-zh，releases 404 且 tags 为空）。兜底必须走 API 而非 `git ls-remote`：本机环境 github.com:443 不可达而 api.github.com 可达（真机实证）。zip 型不做分支兜底——其换装依赖 release tag，无 tag 即无法更新。

执行（二选一，取决于目录形态）：

- **有 `.git`**：`git -C <dir> pull --ff-only`。若本地有未提交修改或发生 divergence → 报错跳过，不强 pull、不 stash。
- **无 `.git`（release zip 流程）**：
  1. 下载 release 资产（zip）到 `mktemp` 临时目录；
  2. 用包内 `SHA256SUMS.txt` 校验（存在则校验，逐文件比对）；
  3. 读取**现装目录**的 `.gitignore`，将其中列出的本地生成文件（anysearch：`runtime.conf`、`.env`）备份；
  4. 校验全部通过后，原子换装：新目录写入 → 回填保留文件 → `rename` 交换 → 旧目录删除。任何一步失败，现装目录不动。

### 4.3 mcp 检测与"打印式"更新

已核实：本机 codegraph / context-mode 均为 `npm i -g` 装入 `/usr/lib/node_modules`（实体目录 + /usr/bin 软链的 npm bin 布局），非 npx。

包名反查：`~/.claude.json` 的 mcpServers 记录的是 bin 命令名（`codegraph`），真实包名可能带 scope（`@colbymchenry/codegraph`）——cpm 扫描 `/usr/lib/node_modules/*/package.json`（含 scoped 的 `@o/p` 两层），匹配 `bin` 字段与命令名一致的包。查不到的（非全局 npm 装法）在 status 标注来源未知、不纳入 check-update。

检测：`npm view <pkg> version`（registry）对比 `/usr/lib/node_modules/<pkg>/package.json` 的 version。
执行：**只打印** `sudo npm i -g <pkg>@<ver>`。cpm 自身永不执行 sudo / 特权写（已定）。
项目级条目（3 处 playwright）：status 如实列出并标注"与官方插件重叠，cpm 不处理"；check-update 不覆盖。

## 5. 输出

- 默认人类可读表格，每行 `类别  名称  当前 -> 最新  状态标记`，例如：

  ```
  $ cpm check-update
  PLUGIN  superpowers   6.3.0(f2cbfb) -> 6.4.x(b36e08)   可更新
  SKILL   anysearch     3.1.1 -> 3.2.0                    可更新(release zip)
  SKILL   humanizer-zh  main(a1b2c3) -> main(d4e5f6)      可更新(git)
  MCP     codegraph     0.2.1 -> 0.2.3                    需手动: sudo npm i -g ...
  MCP     context-mode  1.3.0 -> 1.3.0                    最新
  ```
- `--json`：机器可读，供 Claude Code 会话内直接消费。
- 退出码：0 全部最新且无失败；1 存在可更新项（仅 check-update）；2 存在失败的资产操作或某资产 latest 无法解析；3 环境/状态文件异常。

## 6. 错误处理

- 单资产网络失败（限流、断网、404）：该行 latest 标 `?`，继续处理其余，退出码 2。
- `update --all`：逐资产独立事务，一个失败不影响其余，末尾汇总成功/失败清单。
- 状态文件缺失/JSON 损坏：立即报错退出（码 3），输出期望路径，不做猜测性修复。
- 子进程（claude / git / npm）失败：透传其 stderr 摘要。

## 7. 测试策略

可验证的成功标准：

1. fixture 测试（离线确定性）：`unittest` 构造伪 HOME —— 按 4.1 的三种 plugin source 形态各造一个 marketplace 副本（含真实小 git 仓库），断言 check 结果正确，其中必须覆盖"url 型 sha 不等 → outdated"（对应现实中 superpowers 已可更新的实例）；
2. skill 发现链三级兜底各一个用例；release 换装用例断言 `runtime.conf` / `.env` 被保留、校验失败时现装目录未被改动；
3. 网络层与子进程层通过注入替换，不依赖外网即可跑全量测试；
4. `--dry-run` 对三类资产各覆盖一条执行路径断言（plugin 委托命令行、skill 计划动作、mcp 打印内容）。

测试命令：`python3 -m unittest discover tests/`（项目内 `tests/` 目录）。

## 8. 落地

- 仓库：`~/work/claude/plugin_manager`（本次已 git init），包含 `cpm.py`、`tests/`、`cpm.json`（示例与本机记录）、本 spec。
- 安装：`ln -s <repo>/cpm.py ~/.local/bin/cpm`（README 记载）。
- 本机现状备注（供首跑验收参照）：superpowers 应报可更新（已装 gitCommitSha `f2cbfb…` ≠ marketplace 声明 `b36e08…`）；anysearch 应从 README 自动发现来源。
