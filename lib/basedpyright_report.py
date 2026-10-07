"""Render basedpyright JSON with paths relative to the supplied root."""

import json
import os
import sys
from pathlib import Path
from textwrap import indent
from typing import NotRequired, TypedDict, cast

from rich.console import Console
from rich.padding import Padding
from rich.style import Style
from rich.text import Text
from terminal_env import is_color_forced, is_hyperlink_supported, should_print_pretty

STYLES = {'error': 'red', 'warning': 'yellow', 'information': 'blue'}


class Diagnostic(TypedDict):
    file: str
    severity: str
    message: str
    range: NotRequired[dict[str, dict[str, int]]]
    rule: NotRequired[str]


class Report(TypedDict):
    generalDiagnostics: list[Diagnostic]
    summary: dict[str, int | float]


def render(report: Report, root: Path) -> None:
    forced = is_color_forced()
    pretty = should_print_pretty()
    colored = pretty and ('NO_COLOR' not in os.environ or forced)
    hyperlinks = colored and is_hyperlink_supported()
    console = Console(
        force_terminal=colored,
        color_system='standard' if colored else None,
        no_color=not colored,
        highlight=False,
    )
    for diagnostic in report['generalDiagnostics']:
        filename = diagnostic['file']
        path = os.path.relpath(filename, root) if filename else 'project'
        start = diagnostic.get('range', {}).get('start')
        location = f':{start["line"] + 1}:{start["character"] + 1}' if start else ''
        position = path + location
        severity = diagnostic['severity']
        rule_name = diagnostic.get('rule')
        rule = f' [{rule_name}]' if rule_name else ''
        if pretty:
            first, _, detail = diagnostic['message'].partition('\n')
            link = None
            if hyperlinks and filename:
                link = (root / filename).as_uri()
                if os.environ.get('TERM_PROGRAM', '').lower() == 'zed':
                    link = 'zed://file' + link.removeprefix('file://') + location
            console.print(Text(position, style=Style(bold=True, link=link)), soft_wrap=True)
            message = Text()
            _ = message.append(severity, style=STYLES[severity])
            _ = message.append(f': {first}')
            _ = message.append(rule, style='dim')
            if detail:
                _ = message.append('\n' + indent(detail, '  '), style='dim')
            console.print(Padding(message, (0, 0, 0, 2)))
            console.print()
        else:
            print(f'{position}: {severity}: {" ".join(diagnostic["message"].splitlines())}{rule}')
    counts = report['summary']
    summary = f'{counts["errorCount"]} errors, {counts["warningCount"]} warnings, {counts["informationCount"]} notes'
    console.print(Text(summary, style='bold red' if counts['errorCount'] else 'bold green'), soft_wrap=True)


if __name__ == '__main__':
    try:
        render(cast(Report, json.load(sys.stdin)), Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve())
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        sys.exit(f'basedpyright-report: {error}')
