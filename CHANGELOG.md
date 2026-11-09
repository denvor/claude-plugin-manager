# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 形式。
尚未打 tag，版本按发布日期标记。日期为提交日期（`git log --date=short`）。

## 2026-11-09 — status 表尾显示快照距今时长

### Added

- **`status` 表尾注明快照距今多久**：注脚由「状态来自 … 的 check-update」改为「…，距今1天20小时
  （离线快照，cpm check-update 刷新）」。`human_age()` 把 `checked_at` 格式化为
  「刚刚 / N分钟 / N小时 / N天M小时」；无法解析或时间在未来返回空串（不阻塞 status）。

## 2026-11-09 — cpm.json 代理配置

### Added

- **`cpm.json` 的 `proxy.{enabled,url}`**：cpm 读取后自行决定走不走代理——`fetch_url` 用
  `ProxyHandler` 注入，`run_cmd` 把 `http_proxy`/`https_proxy` 注入子进程环境，于是 urllib
  通道（skill 检查、skill zip 换装）与 npm/git 等外部工具都跟随配置。`enabled` 为 false 或缺
  `url` 一律直连；未配置时 urllib 仍读环境变量 `https_proxy`。真实动机：本机 GitHub 系域名
  直连不通/不稳（api.github.com 直连实测 0/5，走本地代理 5/5），而 `fetch_url` 此前是裸
  `urlopen`，既不读 gitconfig 也不读 cpm 配置。（`proxy_from_config()`，`Ctx` 载入模块级
  `_PROXY_URL`）

### Docs

- README「网络与代理」改用 `cpm.json` 配置说明；skill 检查/换装两行从「须带环境变量」改为
  「须在 cpm.json 启用 proxy」。

## 2026-11-09 — Linux 一键部署（install.sh）

### Added

- **`install.sh`**：Linux/macOS 一键部署，与 `install.ps1` 对应。在 `~/.local/bin` 建 symlink
  指向仓库 `cpm.py`（`git pull` 即生效），并确保该目录在持久 PATH 里：先跑 `$SHELL -l -c` 探测
  登录 shell 的 PATH，缺则把带标记的 `[ -d "$HOME/.local/bin" ] && PATH=…` 块写进 `~/.profile`
  （zsh 为 `~/.zprofile`；已写过则提示未生效，不重复写），最后跑 `cpm --help` 自检，并把该目录
  临时插进当前窗口 PATH。`~/.local/bin/cpm` 若被真实文件占用则备份为 `*.bak` 再建 symlink。
  （`133ca3a`）

### Docs

- README 安装段的 Linux/macOS 一部分由手写 symlink 命令改为 `./install.sh`。

## 2026-11-09 — status 快照与 Windows 一键部署

### Added

- **`status` 离线显示上次 `check-update` 的判定**：比对完成后结果写入
  `~/.claude/.cpm_last_check.json`（`checked_at` + 每资产的 current/expected/status/managed/notes），
  `status` 读该快照——命中行直接显示「最新 / 可更新」与应有版本，未收录资产仍显示「待检测」，
  表尾注明快照时间。缓存缺失或损坏时 `status` 行为不变（绝不因缓存坏掉而走退出码 3）。（`b81e813`）
- `cache_key()` / `load_cache()` / `write_cache()`：plugin 以 marketplace id 为键
  （不同 marketplace 可重名），其余资产用名称。（`b81e813`）
- **`status` 无快照时给出提示**：表尾注脚改为二选一——有快照仍注「状态来自 … 的 check-update」，
  无快照（含缓存损坏，视同缺失）则提示「运行 cpm check-update 后此处显示判定快照」。
  条件写作 `elif not checked`：`check-update` 自己也是以 `checked=True` 调 `render`，
  只判缓存会让联网跑完反而冒出一句「无离线快照」。（`2bc0510`）
- **`install.ps1`**：Windows 一键部署。生成 `cpm.cmd`（cmd/PowerShell）与 `cpm`（Git Bash）
  两个 shim，把 `~/.local/bin` 写入 `HKCU\Environment`（幂等，识别 `%VAR%` 旧条目），
  临时插入当前窗口 PATH，并跑 `cpm.cmd --help` 自检。shim 用无 BOM 的 CRLF（.cmd）/ LF（sh）
  写入；改 PATH 用 `SetEnvironmentVariable` 而非 `setx`（后者截断 1024 字符）。脚本自身带
  UTF-8 BOM——Windows PowerShell 5.1 把无 BOM 文件按 ANSI 码页读，中文注释与输出会烂。
  （`2bc0510`）

### Fixed

- `cpm update` 成功改动资产后删除缓存：否则刚更新完 `status` 仍显示「可更新」。
  dry-run 与全部失败的更新不动缓存（状态未变，快照仍有效）。（`b81e813`）
- **`test_status_cache` 在 Windows 报错**：`shutil.rmtree` 删 git 仓库撞上 Git for Windows
  的只读 loose object（`PermissionError WinError 5`），改用仓库自带的 `fixture.rm_rf`；
  同一文件里「无缓存不注脚」的旧断言改为断言新提示。（`2bc0510`）

### Docs

- README 安装段改用 `install.ps1`，保留「部署目录必须在注册表 PATH」的警告。（`2bc0510`）

### Chore

- **测试套件补齐 npm 注入**：`check_plugins` 的 npm 辅助通道与 `load_mcps` 的能力探测在
  部分用例里未注入，会真发 `npm view` / `npm config ls -l`（registry 抽风时全套从 3s
  拖到 130s+）。给 TestPathWithoutDotGit、TestCloneVersionFirst、TestContentBasisVisible、
  TestRestoreSkill、TestRestoreConflicts 补 npm 桩；`_allow_scripts_probe` 各处 cleanup
  统一恢复 False，不再留 None 让后续测试走真探测。全量 106 tests 约 2.6s，探测器
  （拦 npm/claude/fetch）复跑零命中。（`e63f46d`）

## 2026-10-19 — 全新环境可用性

### Fixed

- **零插件环境误报状态异常**：`installed_plugins.json` 不存在时不再抛 `Broken`（退出码 3）。
  全新 Claude Code 不会创建空账本，文件缺失即「零个已装 plugin」；JSON 损坏仍按环境异常退出。（`197b2d0`）

## 2026-10-18 — 浅克隆下的 plugin 假阳性

### Fixed

- **路径型 plugin 在浅克隆下被集体误报「可更新」**：官方 marketplace 克隆改为浅克隆
  （全仓库仅 1 个提交）后，`git log -- <path>` 对任何路径都返回同一个浅根，四个无 semver
  的插件被同一提交哈希判定为可更新（真机 `diff -r` 实证内容早已最新）。`plugin_expected`
  在 entry 与 clone 都无 semver 时改用**文件树 sha256 内容比对**（安装目录 vs clone 路径，
  排除 `.in_use` / `.orphaned_at` 生命周期标记与 `node_modules`，毫秒级零下载）：一致 →
  应有版本 = 现有；不一致 → 回落比较 clone HEAD；结论写入 notes。`check_plugins` 接线传
  `installPath` / `current`，旧签名向后兼容。（`a0b9853`）
- **`restore` 往返测试依赖宿主 npm**：漏锁 `_allow_scripts_probe`，真机 npm v12 探测为 True
  时击穿公共形态的子串断言。（`a0b9853`）

### Docs

- README「各类别」的 plugin 条目改为三级基准（semver / 钉定 sha / 内容比对），说明浅克隆
  使「path 最后提交」语义失效及其原因。（`a0b9853`）

## 2026-10-12 — 卸载三件套 + Win/Linux 全链路兼容

### Added

- **`uninstall` / `trash` / `restore`**（`c478810`）：
  - skill：整目录移入 `~/.claude/.cpm_trash/<时间戳>-<name>/`。回收目录在 `skills/` 之外，
    不会被 Claude Code 当技能加载；同秒重名自动加 `-2` 后缀，绝不覆盖。`cpm.json` 的来源补录保留。
  - MCP：从 `~/.claude.json` 删除 `mcpServers.<name>`，删除前把完整条目存档；包本体**只打印**
    `npm rm -g <pkg>`，cpm 永不执行特权命令。
  - plugin：委托官方 `claude plugin uninstall`——官方自删缓存与数据且不留备份，`restore` 无法恢复。
  - `restore`：skill 移回原位并清除回收标记，MCP 原样写回存档；目标已存在时**拒绝覆盖**
    （退出码 2，回收条目保留）；同名多条取最新。
- `npm_global_remove_cmd()`：卸载命令与安装命令同源，`sudo` 前缀按平台加（`329629c`）。
- uninstall/restore 一并遵循兼容约定：IO 显式 UTF-8、`--allow-scripts` 走探测、跨盘安全的
  同父目录改名（`329629c`）。

### Fixed

- **中文 Windows 崩溃**：文件读写与子进程输出改为显式 UTF-8，stdout/stderr 在被管道/重定向时
  `reconfigure(encoding="utf-8")`。此前 `cpm add` 等在 GBK locale 下打印 `✓` 直接抛
  `UnicodeEncodeError`（真终端走 ConsoleIO 不受影响）。（`79b285b`）
- **`.cmd` shim 调不动**：`CreateProcess` 不解析裸名 `npm` / `claude`（npm 在 Windows 生成的是 `.cmd`），
  `run_cmd` 先 `shutil.which(argv[0])` 解析后再执行；Linux 行为不变。
- **全局 npm 目录找错**：按平台取默认（`%APPDATA%\npm\node_modules` vs
  `/usr{,/local}/lib/node_modules`），候选皆不存在时 `npm root -g` 兜底。
- **zip 换装跨盘卷失败**：临时解包目录改放目标同父目录（`rename` 跨卷 Linux 报 EXDEV、
  Windows 报 ERROR_NOT_SAME_DEVICE）；换装失败回滚原目录，绝不出现"资产消失"。
- **删目录 WinError 5**：Git for Windows 把 loose object 标为只读，`shutil.rmtree` 直接抛错；
  统一走解只读重试（`_rmtree_force`，`onexc`/`onerror` 按 Python 版本分支）。
- **`--allow-scripts` 警告**：打印的 npm 安装命令不再无条件带该 flag——旧 npm（真机 11.12.1）报
  `Unknown cli config`，npm v12 中未知 CLI flag 直接报错。改为探测
  `npm config ls -l` 是否含 `allow-scripts` 项，结果进程内缓存。
- **两条 sudo 硬断言**（`tests/test_uninstall.py`）：`assertIn("sudo npm rm -g")` 与
  `assertIn("sudo npm i -g")` 在 Windows 必红——代码已正确省掉 sudo，是测试假设了 Linux 形态。
  改为锁跨平台公共命令形态，并单独断言 sudo 这一轴的有无随 `os.name` 变化。
- **项目级 MCP 的 notes 一刀切**：`load_mcps` 给所有项目级条目统一写「项目级配置；与官方插件重叠」，
  而代码从未做过重叠判定——真机那条 `context-mode@D:/` 其实是与**全局条目重复注册**（local 作用域），
  跟插件毫无关系。改为按实际来源标注：同名同命令 → 「与全局条目重复」；同名不同命令 →
  「同名但命令不同（本项目覆盖全局）」；server 名命中官方插件 cache 的 `.mcp.json` 声明 →
  「与官方插件重叠」；都不沾 → 只写「项目级配置」。新增 `plugin_mcp_names(ctx)`（从账本
  `installPath` 收集插件声明的 server 名，无账本返回空集不炸）。fixture 的 cache `.mcp.json`
  补一个 `playwright` server，使「与官方插件重叠」成为真判定而非文案；测试 78→83 全绿。

### Changed

- MCP 安装命令的 `sudo` 前缀按平台加（Windows 无 `sudo`），cpm 自身仍永不使用特权。
- README：MCP 条目改述为 `npm i -g [--allow-scripts=<pkg>] <pkg>@<ver>`，"仅打印 sudo 命令"→"仅打印安装命令"。

### Docs

- README 增补 `uninstall/trash/restore` 命令表行与「卸载与回收站（uninstall / trash / restore）」一节。（`c478810`）
- README 补 Windows 安装（双 shim 形态）、部署目录必须在**注册表 PATH**的警告（Git Bash 会虚增
  `$HOME/bin`，在它内部验证通过而 PowerShell 里 `CommandNotFoundException`，真机踩过）、平台注意一节。
- README「各类别」拆出 **MCP（项目级）** 条目：说明 local 作用域的来源，以及四种 notes 标注各自的判定条件。
- 新增本变更日志。

## 2026-10-11 — 检测基准修正与可观测性

### Added

- `check-update` / `update` 向 stderr 输出阶段进度与逐资产动态（盘点计数、marketplace 刷新状态、
  耗时、可更新数），stdout 保持干净可管道。（`7c45bd8` `880bb82`）
- 打印的 npm 更新命令默认带 `--allow-scripts=<pkg>`。（`dd200a7`；本版语义见 2026-10-12 的探测改造）
- plugin 表格在 sha 比对形态下左列显示 `语义版本/短sha` 双标识。（`33f7750`）

### Fixed

- **superpowers 假阳性**：plugin 比对对象改用账本 `gitCommitSha` 而非展示用 `version`；
  `expected` 为 sha 形态时左列同步显示账本短 sha，与右列同一标识系。（`a1a95c1` `dc9d5b3`）
- **路径型 plugin 的应有版本**：一律优先取 clone 内 `plugin.json` 的 `version`（官方 `up_to_date`
  的真实基准），无 `.git` 与有 `.git` 两条路径都改；无 `version` 才回落 git log / 远端 API。
  （`42f621c` `50ea26e`）
- **无 `.git` 兜底不再比 HEAD**：改查远端该 plugin 目录的最后一次提交，并跳过 merge commit
  （HEAD 兜底对 code-simplifier 产生假阳性）。（`e397ded` `a1a95c1`）
- **npm 辅助通道加身份校验**：只认同包且 `repository.url` 与声明一致的版本，避免同名占位包
  （真机：`superpowers`=0.0.2 是无关包）被当作 npm 通道版本。（`482813d`）

### Docs

- README 补本机网络形态（`github.com:443` 直连不可达）与各通道对代理的依赖表。（`0e78859`）

## 2026-10-10 — 初版

### Added

- 设计文档与 10 任务 TDD 实施计划；网络层（`fetch_url`）与子进程层（`run_cmd`）收敛为模块级函数，
  测试注入后全离线运行。（`91a6b87` `b4d1836`）
- 骨架：`Ctx` / `Asset` / 伪 HOME fixture。（`3d64d0b`）
- plugin：账本加载、`status` 表格渲染、`enabledPlugins` 白名单语义；应有版本三形态比对
  （semver / url 钉 sha / 路径 git log）。（`2cae1c1` `446d649` `4b69d8f`）
- `check-update` 全流程：marketplace 刷新、三形态比对、npm/npx 辅助标注、退出码分级。（`0f2bc85`）
- skill：来源发现链（`cpm.json` 补录 → `.git` remote → README GitHub URL）、最新版三级检测
  （release tag → 分支兜底走 GitHub API）、更新执行（`git pull --ff-only`；release 安装的走
  zip 下载 + SHA256SUMS 校验 + 换装保留 `.gitignore` 所列本地文件）。（`2175572` `b266385`
  `7238e6d` `6a50ec2` `80243ec`）
- mcp 适配器：bin 名→包名反查、`npm view` 检测、**只打印**安装命令（自身永不使用特权）；
  项目级与插件重叠配置仅标注不处理。（`f0647e4`）
- `update` 编排（`--all` 逐项失败不中断 + 汇总、`--dry-run`）与 `add` 来源补录；
  目录尚不存在的 skill 给出"延迟生效"警告。（`03f55ac` `025c998`）

### Fixed

- `run_cmd` 超时返回码 124 而非抛异常；marketplace 刷新/更新放宽到 300s（真机实测 git 可超 60s）。（`5487374`）
- 分支兜底改走 API `commits/HEAD`——本环境 `github.com:443` 不可达，`ls-remote` 不可用。（`6a50ec2`）

### Docs / Chore

- README、spec 增补 skill 最新版三级检测链；补记执行偏差与 `filter-branch` 历史重写。（`01de53c` `ff080d3` `e3b56de`）
- `.gitignore` 忽略 `__pycache__`。（`dd8693b`）
