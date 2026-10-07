"""Shared terminal detection."""

import os
import sys

AGENT_ENVS: set[str] = {
    'CLAUDECODE',
    'CURSOR_AGENT',
    'GEMINI_CLI',
    'CODEX_THREAD_ID',
    'OPENCODE',
    'IN_CLANKER',
    'in-clanker',
}


def is_tty() -> bool:
    return sys.stdout.isatty()


def is_clanker() -> bool:
    return any(os.environ.get(name) for name in AGENT_ENVS)


def is_color_forced(variables: tuple[str, ...] = ('FORCE_COLOR', 'CLICOLOR_FORCE')) -> bool:
    return any(os.environ.get(name, '0') not in ('', '0') for name in variables)


def should_print_pretty() -> bool:
    return (is_tty() and not is_clanker()) or is_color_forced()


def is_hyperlink_supported() -> bool:
    """Detect OSC 8 support; FORCE_HYPERLINK overrides detection."""
    if 'FORCE_HYPERLINK' in os.environ:
        return os.environ['FORCE_HYPERLINK'] not in ('', '0')
    if os.environ.get('TERM') == 'dumb':
        return False
    return (
        os.environ.get('TERM_PROGRAM', '').lower() in {'zed', 'vscode', 'iterm.app', 'wezterm', 'ghostty'}
        or os.environ.get('TERM') in {'xterm-kitty', 'foot', 'foot-extra', 'alacritty'}
        or bool(os.environ.get('WT_SESSION'))
        or (os.environ.get('VTE_VERSION', '').isdigit() and int(os.environ['VTE_VERSION']) >= 5000)
    )
