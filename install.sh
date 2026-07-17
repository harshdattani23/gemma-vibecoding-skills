#!/bin/sh
# Install gemma-coder into every agent skill directory present on this machine.
# Usage: ./install.sh [--copy]   (default: symlink, so git pull updates everywhere)
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
case "$#:${1:-}" in
    0:) MODE=link ;;
    1:--copy) MODE=copy ;;
    *)
        echo "Usage: ./install.sh [--copy]" >&2
        exit 2
        ;;
esac

install_into() {
    parent="$1"; target="$2/gemma-coder"
    [ -d "$parent" ] || return 0            # agent not installed on this machine
    mkdir -p "$2"
    rm -rf "$target"
    if [ "$MODE" = "copy" ]; then
        cp -R "$SRC" "$target"
    else
        ln -s "$SRC" "$target"
    fi
    echo "installed -> $target"
}

# Claude Code
install_into "$HOME/.claude"        "$HOME/.claude/skills"
# Codex CLI (Agent Skills open standard global dir)
install_into "$HOME/.codex"         "$HOME/.agents/skills"
# Antigravity / Antigravity CLI / Gemini
install_into "$HOME/.gemini"        "$HOME/.gemini/config/skills"
# Generic open-standard location, if some other agent already created it
[ -d "$HOME/.agents/skills" ] && install_into "$HOME/.agents" "$HOME/.agents/skills"

# Expose a normal user command. Copy mode uses a canonical copy so the command
# remains valid after the source checkout is removed.
mkdir -p "$HOME/.local/bin"
if [ "$MODE" = "copy" ]; then
    CANONICAL="$HOME/.local/share/gemma-coder"
    mkdir -p "$HOME/.local/share"
    rm -rf "$CANONICAL"
    cp -R "$SRC" "$CANONICAL"
    ln -sf "$CANONICAL/gemma-coder.sh" "$HOME/.local/bin/gemma-coder"
else
    ln -sf "$SRC/gemma-coder.sh" "$HOME/.local/bin/gemma-coder"
fi
echo "installed -> $HOME/.local/bin/gemma-coder"

echo "Done. Run 'gemma-coder setup' (or 'python3 $SRC/scripts/setup.py') to pick your model."
