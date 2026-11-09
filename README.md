# cpm — Claude Code 资产管理 CLI

盘点 / 检查更新 / 执行更新 Claude Code 的 plugin、skill、MCP 三类扩展资产。
设计文档：`docs/superpowers/specs/2026-10-10-cpm-design.md`；变更历史：`CHANGELOG.md`

要求：Python ≥ 3.10（用了 `str|None` 标注与 `removesuffix`）、`git`、`npm`、已配置 Claude Code。
Windows / Linux 双平台可用（兼容要点见「平台注意」）。

## 安装

cpm 是单文件脚本，安装=把它放上 PATH；shim/symlink 指向仓库里的 `cpm.py`，所以以后 `git pull` 即生效，无需重装。

Windows（在仓库根目录执行；脚本生成两个 shim 并把 `~/.local/bin` 写入**注册表** PATH）：

    powershell -ExecutionPolicy Bypass -File .\install.ps1

Linux / macOS（在仓库根目录执行；建 symlink 并把 `~/.local/bin` 写进 shell 配置）：

    ./install.sh

> ⚠ **部署目录要在注册表 PATH（`HKCU\Environment\Path`）里，不能只在 shell 里 `echo %PATH%` 验证。**
> Git Bash 会把自己的 `$HOME/bin` 注入到**它那个进程**的 PATH，即使该目录不在系统 PATH 中——
> 在它内部一切正常，切到 PowerShell 就是 `CommandNotFoundException`（真机踩过）。
> `install.ps1` 写的就是这份持久 PATH；手动部署时自己确认：`reg query HKCU\Environment /v Path`。

## 命令

| 命令 | 说明 | 联网 |
|---|---|---|
| `cpm status` | 盘点三类资产（版本/来源/启用/可管理性）；显示上次 check-update 缓存的判定 | 否 |
| `cpm check-update [--json]` | 刷新 marketplace 并比对可更新项 | 是 |
| `cpm update <name>…` / `cpm update --all [--dry-run]` | 执行更新 | 视类别 |
| `cpm uninstall <name>… [--dry-run]` | 卸载资产（skill/MCP 入回收站，plugin 委托官方） | 否 |
| `cpm trash` | 列出回收站条目 | 否 |
| `cpm restore <name>` | 从回收站恢复 skill 目录 / MCP 配置 | 否 |
| `cpm add <skill> <url>` | 补录无法自动发现的 skill 来源 | 否 |

退出码：0 无事项；1 有可更新项；2 有失败/检测不到；3 状态文件异常。

`status` 是离线的：它显示的是**上次 `cpm check-update` 的判定快照**，缓存在
`~/.claude/.cpm_last_check.json`（`checked_at` + 每资产的 current/expected/status/managed/notes）。
命中缓存的行直接给出「最新 / 可更新」与应有版本，未收录的资产仍显示「待检测」，表尾注明
快照时间与距今时长（如「距今1天20小时」）。缓存缺失或损坏时 `status` 行为不变（绝不因缓存
坏掉而退出码 3）。`cpm update`
成功改动资产后会删除缓存（刚被改动的「可更新」已失效），dry-run 与全部失败的更新不动缓存。

## 各类别的更新通道（边界，重要）

- **plugin**：检测按三级基准——① entry/clone `plugin.json` 有 semver 就比版本；
  ② entry `url` 型比钉定 sha；③ 路径型无版本→**内容比对**：官方 marketplace 克隆已是
  浅克隆（全仓库 1 个提交），「path 最后提交」语义失效（真机实证：四个无版本插件被
  同一浅根提交集体误报可更新）。比对 = 安装目录 vs clone 路径的文件树 sha256
  （排除官方 `.in_use/`、`.orphaned_at` 生命周期标记与 `node_modules`，毫秒级、零下载），
  结论写进 notes（`path 内容与安装版一致` / `较安装版有变化`）。
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

本机 **GitHub 系域名（github.com / api.github.com / codeload.github.com）直连不通或不稳**，git 系访问须走本地代理（全局 gitconfig 已配
`http.https://github.com.proxy = http://127.0.0.1:20171`）；npm registry 直连可达（走代理也通）。
cpm 在 `cpm.json` 里读代理配置，据此决定走不走（urllib 通道与子进程环境变量都生效）：

    {
      "skills": {},
      "proxy": { "enabled": true, "url": "http://127.0.0.1:20171" }
    }

`enabled` 为 false 或缺 `url` 一律直连；未配置时 urllib 仍会读环境变量 `https_proxy`。

各通道对代理的依赖：

| cpm 动作 | 走哪里 | 不走代理会怎样 |
|---|---|---|
| check-update：skill 最新版 | api.github.com（urllib） | **失败**——直连不稳，须在 cpm.json 启用 proxy |
| check-update：mcp | npm registry | 不受影响（直连可达，走代理也通） |
| check-update：marketplace 刷新 | 官方 CLI → git → github.com | 刷新超时→降级告警，比对照常（结果可能偏旧） |
| update：plugin | 官方 CLI → git | **失败**，需代理存活 |
| update：skill zip 换装 | codeload.github.com（urllib） | **失败**——直连不稳，须在 cpm.json 启用 proxy |
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
