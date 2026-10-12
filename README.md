# cpm — Claude Code 资产管理 CLI

盘点 / 检查更新 / 执行更新 Claude Code 的 plugin、skill、MCP 三类扩展资产。
设计文档：`docs/superpowers/specs/2026-10-10-cpm-design.md`

## 安装

    ln -s "$HOME/work/claude/plugin_manager/cpm.py" ~/.local/bin/cpm

要求：Python ≥ 3.10（用了 `str|None` 标注与 `removesuffix`）、`git`、`npm`、已配置 Claude Code。

## 命令

| 命令 | 说明 | 联网 |
|---|---|---|
| `cpm status` | 盘点三类资产（版本/来源/启用/可管理性） | 否 |
| `cpm check-update [--json]` | 刷新 marketplace 并比对可更新项 | 是 |
| `cpm update <name>…` / `cpm update --all [--dry-run]` | 执行更新 | 视类别 |
| `cpm uninstall <name>… [--dry-run]` | 卸载资产（skill/MCP 入回收站，plugin 委托官方） | 否 |
| `cpm trash` | 列出回收站条目 | 否 |
| `cpm restore <name>` | 从回收站恢复 skill 目录 / MCP 配置 | 否 |
| `cpm add <skill> <url>` | 补录无法自动发现的 skill 来源 | 否 |

退出码：0 无事项；1 有可更新项；2 有失败/检测不到；3 状态文件异常。

## 各类别的更新通道（边界，重要）

- **plugin**：检测=本地比对（marketplace git 副本 vs `installed_plugins.json` 账本）；
  执行=官方 `claude plugin update`。**不做 npm 覆盖 cache**（会破坏账本）。
- **skill**：来源自动发现（`.git` remote → README GitHub URL → `cpm.json` 补录）。
  有 `.git` 的 `pull --ff-only`；release 安装的走 zip 下载 + SHA256SUMS 校验 +
  换装时保留 `.gitignore` 所列本地文件（如 runtime.conf / .env）。
- **MCP（npm 全局装法）**：检测=`npm view`；执行**只打印**
  `npm i -g [--allow-scripts=<pkg>] <pkg>@<ver>`（Linux/macOS 自动前缀 `sudo`，Windows 无 sudo；
  `--allow-scripts` 仅在探测到当前 npm 支持该配置项时才加——npm v12 系默认跳过 install scripts
  必须显式放行（真机教训：context-mode 的 postinstall/自愈例程被跳、better-sqlite3 原生构建靠缓存
  侥幸可用），而旧 npm（真机 11.12.1）不识别该 flag，硬带会报 Unknown cli config 警告），
  cpm 自身永不使用特权。项目级 MCP 与插件重叠配置不处理，仅标注。

## 卸载与回收站（uninstall / trash / restore）

- **plugin**：委托官方 `claude plugin uninstall <id>`。官方自删缓存与数据目录、
  **不留备份**，`cpm restore` 无法恢复——重装用 `claude plugin install <id>`。
- **skill**：整个目录移入 `~/.claude/.cpm_trash/<时间戳>-<name>/`（回收目录在
  `skills/` 之外，不会被 Claude Code 当技能加载；同秒重名自动加 `-2` 后缀，绝不覆盖）。
  `cpm.json` 的来源补录**保留**，restore 后来源信息完整。
- **MCP（npm 全局装法）**：从 `~/.claude.json` 删除 `mcpServers.<name>`，删除前把
  完整条目存档到回收站；**只打印** `sudo npm rm -g <pkg>`（restore 同理只打印
  `sudo npm i -g --allow-scripts=<pkg> <pkg>@<ver>`），cpm 永不执行特权命令。
- **restore**：skill 恢复 = 目录移回原位并清除回收标记；MCP 恢复 = 存档条目原样写回。
  目标已存在时**拒绝覆盖**（退出码 2，回收条目保留）。同名多条取最新。
- 清空回收站无专门命令：`rm -rf ~/.claude/.cpm_trash`。

## 网络与代理（本机环境）

本机 **github.com:443 直连不可达**，git 系访问须走本地代理（全局 gitconfig 已配
`http.https://github.com.proxy = http://127.0.0.1:20171`）；`api.github.com` 与 npm registry 直连可达。

各通道对代理的依赖：

| cpm 动作 | 走哪里 | 代理挂了会怎样 |
|---|---|---|
| check-update：skill 最新版 | api.github.com（urllib 直连） | 不受影响 |
| check-update：mcp | npm registry 直连 | 不受影响 |
| check-update：marketplace 刷新 | 官方 CLI → git → github.com | 刷新超时→降级告警，比对照常（结果可能偏旧） |
| update：plugin | 官方 CLI → git | **失败**，需代理存活 |
| update：skill zip 换装 | codeload.github.com（urllib） | **失败**——urllib 不读 gitconfig，须显式带环境变量：`https_proxy=http://127.0.0.1:20171 cpm update --all` |
| update：mcp | 仅打印安装命令 | 执行时 npm 直连可达 |

## 开发

测试（全离线，注入网络层）：

    python3 -m unittest discover -s tests -t . -v
