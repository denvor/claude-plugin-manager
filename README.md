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
| `cpm add <skill> <url>` | 补录无法自动发现的 skill 来源 | 否 |

退出码：0 无事项；1 有可更新项；2 有失败/检测不到；3 状态文件异常。

## 各类别的更新通道（边界，重要）

- **plugin**：检测=本地比对（marketplace git 副本 vs `installed_plugins.json` 账本）；
  执行=官方 `claude plugin update`。**不做 npm 覆盖 cache**（会破坏账本）。
- **skill**：来源自动发现（`.git` remote → README GitHub URL → `cpm.json` 补录）。
  有 `.git` 的 `pull --ff-only`；release 安装的走 zip 下载 + SHA256SUMS 校验 +
  换装时保留 `.gitignore` 所列本地文件（如 runtime.conf / .env）。
- **MCP（npm 全局装法）**：检测=`npm view`；执行**只打印** `sudo npm i -g <pkg>@<ver>`，
  cpm 自身永不使用特权。项目级 MCP 与插件重叠配置不处理，仅标注。

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
| update：mcp | 仅打印 sudo 命令 | 执行时 npm 直连可达 |

## 开发

测试（全离线，注入网络层）：

    python3 -m unittest discover -s tests -t . -v
