# cpm — Claude Code 资产管理 CLI

盘点 / 检查更新 / 执行更新 Claude Code 的 plugin、skill、MCP 三类扩展资产。
设计文档：`docs/superpowers/specs/2026-10-10-cpm-design.md`；变更历史：`CHANGELOG.md`

要求：Python ≥ 3.10（用了 `str|None` 标注与 `removesuffix`）、`git`、`npm`、已配置 Claude Code。
Windows / Linux 双平台可用（兼容要点见「平台注意」）。

## 安装

cpm 是单文件脚本，安装=把它放上 PATH；shim 指向仓库里的 `cpm.py`，所以以后 `git pull` 即生效，无需重装。
部署目录 `~/.local/bin` 需已在 PATH 中（Windows 上必须是**注册表**里的那份，见下方警告）。

Linux / macOS（symlink）：

    ln -s "$HOME/work/claude/claude-plugin-manager/cpm.py" ~/.local/bin/cpm

Windows（在 Git Bash 里执行；把仓库路径换成你机器上的位置）——同一目录放两个 shim，
`cpm.cmd` 给 cmd/PowerShell（PATHEXT 命中它），无扩展名的 `cpm` 给 Git Bash：

    printf '@echo off\r\npython "D:\\work\\claude\\claude-plugin-manager\\cpm.py" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' > ~/.local/bin/cpm.cmd
    printf '#!/bin/sh\nexec python "D:/work/claude/claude-plugin-manager/cpm.py" "$@"\n' > ~/.local/bin/cpm
    chmod +x ~/.local/bin/cpm

> ⚠ **部署目录要在注册表 PATH（`HKCU\Environment\Path`）里，不能只在 shell 里 `echo %PATH%` 验证。**
> Git Bash 会把自己的 `$HOME/bin` 注入到**它那个进程**的 PATH，即使该目录不在系统 PATH 中——
> 在它内部一切正常，切到 PowerShell 就是 `CommandNotFoundException`（真机踩过）。
> 查持久化的那份：`reg query HKCU\Environment /v Path`。

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
  cpm 自身永不使用特权。
- **MCP（项目级）**：Claude Code 的 local 作用域（在 `~/.claude.json` 的项目键下）**不处理，只标注**，
  且 notes 按实际来源判定而非一刀切：与全局条目同名同命令 → 「与全局条目重复」；同名但命令不同 →
  「同名但命令不同（本项目覆盖全局）」；server 名命中官方插件 cache 里 `.mcp.json` 的声明 →
  「与官方插件重叠」；都不沾 → 只写「项目级配置」。真机教训：`context-mode@D:/` 是**重复注册**，
  与插件无关，旧文案却统一写「与官方插件重叠」。

## 卸载与回收站（uninstall / trash / restore）

- **plugin**：委托官方 `claude plugin uninstall <id>`。官方自删缓存与数据目录、
  **不留备份**，`cpm restore` 无法恢复——重装用 `claude plugin install <id>`。
- **skill**：整个目录移入 `~/.claude/.cpm_trash/<时间戳>-<name>/`（回收目录在
  `skills/` 之外，不会被 Claude Code 当技能加载；同秒重名自动加 `-2` 后缀，绝不覆盖）。
  `cpm.json` 的来源补录**保留**，restore 后来源信息完整。
- **MCP（npm 全局装法）**：从 `~/.claude.json` 删除 `mcpServers.<name>`，删除前把
  完整条目存档到回收站；**只打印** `npm rm -g <pkg>`（restore 同理只打印重装命令，
  命令形态与「各类别」一节的 sudo / `--allow-scripts` 规则一致），cpm 永不执行特权命令。
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

## 平台注意（Windows）

- **编码**：文件读写、子进程输出、被管道/重定向时的 stdout 全部显式 UTF-8（中文 Windows 默认 GBK，
  `✓` 一类符号会直接抛 `UnicodeEncodeError`）。代价是在 PowerShell **内部再管道**
  （`cpm status | findstr x`）时中文乱码——PowerShell 用控制台码页解码子进程字节流，不是 cpm 的缺陷；
  先 `chcp 65001` 或 `[Console]::OutputEncoding=[Text.Encoding]::UTF8`。直接敲 `cpm status` 走 ConsoleIO，不受影响。
- **`python3` 别用**：Windows 上它常是 Microsoft Store 的别名占位（报 "Python was not found"），一律用 `python`。
- **全局 npm 目录**：默认按平台取 `%APPDATA%\npm\node_modules`（Windows）/ `/usr{,/local}/lib/node_modules`，
  候选都不存在时用 `npm root -g` 兜底；仍不对可显式指定。

## 开发

测试（全离线，注入网络层；Windows 用 `python`，见「平台注意」）：

    python3 -m unittest discover -s tests -t . -v
