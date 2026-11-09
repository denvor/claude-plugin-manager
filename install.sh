#!/usr/bin/env bash
# 部署 cpm：在 ~/.local/bin 建 symlink 指向仓库 cpm.py，并确保该目录在持久 PATH 里。
# 与 install.ps1 对应（Linux/macOS 侧）。symlink 指向仓库，以后 git pull 即生效。
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="$repo/cpm.py"
dir="$HOME/.local/bin"

if [ ! -f "$script" ]; then
    echo "错误：找不到 $script（install.sh 应放在仓库根目录）" >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "错误：PATH 里没有 python3" >&2
    exit 1
fi

chmod +x "$script"
mkdir -p "$dir"

# --- 1) 持久 PATH：登录 shell 的 PATH 里没有 ~/.local/bin 就写进 shell 配置（幂等）---
shell="$(basename "${SHELL:-/bin/bash}")"
case "$shell" in
    zsh) profile="$HOME/.zprofile" ;;
    *)   profile="$HOME/.profile" ;;
esac
marker="# cpm: ~/.local/bin 进 PATH"
if "${SHELL:-/bin/bash}" -l -c 'case ":$PATH:" in *":$HOME/.local/bin:"*) exit 0 ;; esac; exit 1' \
        >/dev/null 2>&1; then
    echo "持久 PATH 已含 $dir"
elif grep -qF "$marker" "$profile" 2>/dev/null; then
    echo "$profile 已写过 cpm 的 PATH 配置，但登录 shell 未生效——检查它是否被 $shell 加载"
else
    printf '\n%s\n[ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH"\n' "$marker" >> "$profile"
    echo "已把 $dir 写入 $profile（新开终端生效）"
fi

# --- 2) symlink（指向仓库，git pull 即生效）---
link="$dir/cpm"
if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "警告：$link 是真实文件而非 symlink，已备份为 $link.bak" >&2
    mv "$link" "$link.bak"
fi
ln -sfn "$script" "$link"
echo "已部署：$link -> $script"

# --- 3) 当前窗口立即生效，并自检 ---
case ":$PATH:" in *":$dir:"*) ;; *) export PATH="$dir:$PATH" ;; esac
if ! "$link" --help >/dev/null 2>&1; then
    echo "错误：自检失败（$link --help 退出码非 0）" >&2
    exit 1
fi

echo "部署完成：$link -> $script"
echo "新开终端即可使用 cpm；本窗口已临时加入 PATH。"
