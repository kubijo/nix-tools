import concurrent.futures
import contextlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tyro
from common import (
    STATES,
    Failure,
    Result,
    UnreadableSource,
    command_json,
    relative,
    snapshot,
    summary,
    validate_adapter,
)
from network import Client
from providers import PROVIDERS
from rich import box
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.style import Style
from rich.table import Table
from rich.text import Text
from sources import ReleaseEntry, nix_jobs, release_entry, workflow_jobs
from terminal_env import is_clanker, is_color_forced

STATE_STYLES = {
    'error': 'bold red',
    'unknown': 'bright_black',
    'blocked': 'red',
    'outdated': 'yellow',
    'ahead': 'cyan',
    'up-to-date': 'green',
    'pinned': 'blue',
    'skipped': 'bright_black',
}
IDENTIFIER_STYLE = Style(color='bright_white', bgcolor='grey30', bold=True)
type Callback = Callable[[], object]
type Job = tuple[str, str, str, Callback]


class Adapter(TypedDict):
    name: str
    exe: str
    args: list[str]
    root: NotRequired[str]
    timeout: NotRequired[float]


class Inventory(TypedDict):
    providers: Mapping[str, Mapping[str, str]]
    tools: list[ReleaseEntry]
    releases: list[ReleaseEntry]
    adapters: list[Adapter]
    skips: NotRequired[list[ReleaseEntry]]
    concurrency: NotRequired[int]


class Config(Inventory):
    treeRootFile: str
    timeout: float
    githubApi: str
    nvchecker: NotRequired[str]


def clean(value: str) -> str:
    # Native metadata and adapter diagnostics are untrusted terminal text.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    value = ''.join(c if c in '\n\t' or ord(c) >= 32 else '?' for c in value)
    value = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', value)
    value = re.sub(
        r'([?&](?:token|key|password|secret|auth)[^=\s]*=)[^&\s]+', r'\1[redacted]', value, flags=re.IGNORECASE
    )
    for name, secret in os.environ.items():
        if re.search(r'TOKEN|PASSWORD|SECRET|API_KEY', name, re.IGNORECASE) and len(secret) >= 6:
            value = value.replace(secret, '[redacted]')
    return value


def guarded(provider: str, name: str, source: str, callback: Callback) -> list[Result]:
    rows: list[Result] = []

    try:
        value = callback()
        if not isinstance(value, (Result, Iterable)):
            raise Failure('Provider returned an invalid result')
        values = [value] if isinstance(value, Result) else value
        for result in values:
            if (
                not isinstance(result, Result)
                or result.state not in STATES
                or not all(isinstance(value, str) for value in cast(dict[str, object], result.json()).values())
            ):
                raise Failure('Provider returned an invalid result')
            rows.append(result)
        if not rows:
            raise Failure('Provider returned an empty report')
    except Exception as error:  # noqa: BLE001 - preserve other providers and redact raw diagnostics
        detail = (
            str(error)
            if isinstance(error, Failure)
            else f'Lookup failed ({type(error).__name__}); sensitive diagnostics withheld'
        )
        rows.append(
            Result(provider, name, source, 'skipped' if isinstance(error, UnreadableSource) else 'error', detail=detail)
        )

    return rows


def jobs(config: Inventory, root: Path, client: Client) -> list[Job]:
    providers = config['providers']
    output: list[Job] = []

    def add(provider: str, name: str, source: str, callback: Callback) -> None:
        output.append((provider, name, source, callback))

    for provider, settings in providers.items():
        source = settings.get('root', '.')
        try:
            directory = relative(root, source, directory=True)
            if provider in ('nix', 'githubActions'):
                factory = nix_jobs if provider == 'nix' else workflow_jobs
                for name, callback in factory(settings, directory, client):
                    add(provider, name, source, callback)
            else:
                package_provider = PROVIDERS[provider]
                add(
                    provider,
                    provider,
                    source,
                    lambda callback=package_provider, settings=settings, directory=directory: callback(
                        settings, directory, client.timeout
                    ),
                )
        except Exception as error:  # noqa: BLE001 - preserve other providers and redact raw diagnostics
            detail = (
                str(error) if isinstance(error, Failure) else f'Invalid provider inventory ({type(error).__name__})'
            )
            add(
                provider,
                provider,
                source,
                lambda provider=provider, detail=detail, source=source: Result(
                    provider, provider, source, 'error', detail=detail
                ),
            )
    for item in config['tools']:
        add(
            'tools',
            item['name'],
            item.get('source', 'tool inventory'),
            lambda item=item: release_entry(client, item, root=root),
        )

    for item in config['releases']:
        add(
            'releases',
            item['name'],
            'release entry',
            lambda item=item: release_entry(client, item, 'releases', root=root),
        )

    for item in config.get('skips', []):
        add(
            'skips',
            item['name'],
            item.get('source', item['name']),
            lambda item=item: release_entry(client, item, 'skips'),
        )

    for adapter in config['adapters']:

        def execute(adapter: Adapter = adapter) -> list[Result]:
            directory = relative(root, adapter.get('root', '.'), directory=True)
            with snapshot(directory) as work:
                document = command_json(
                    [adapter['exe'], *adapter['args'], '--root', str(work)],
                    work,
                    adapter.get('timeout', client.timeout),
                )
            return validate_adapter(document, 'adapter:' + adapter['name'], adapter.get('root', '.'))

        add('adapter:' + adapter['name'], adapter['name'], adapter.get('root', '.'), execute)

    return output


def find_root(start: Path, marker: str) -> Path:
    current = start.resolve()
    while not (current / marker).exists():
        if current.parent == current:
            raise Failure(f'Could not find repository marker {marker}')
        current = current.parent

    return current


def display_version(provider: str, value: str) -> str:
    if provider == 'nix' and re.fullmatch(r'[0-9a-f]{40,64}', value):
        return value[:10]
    if value.startswith('sha256:') and len(value) > 25:
        return value[:18] + '…'
    return value


def display_name(provider: str, name: str) -> str:
    if provider == 'nix':
        paths = name.split(', ')
        if len(paths) > 1:
            extra = len(paths) - 1
            return f'{paths[0]} (+{extra} {"path" if extra == 1 else "paths"})'
    return name


def note_code(detail: str, used: set[str]) -> str:
    clause = re.split(r'[.;:]\s+', detail, maxsplit=1)[0]
    words = [match[0] for match in re.finditer(r'[a-z0-9]+', clause.lower())]
    selected: list[str] = []
    for word in words[:4]:
        candidate = '-'.join([*selected, word])
        if len(candidate) > 24:
            break
        selected.append(word)
    base = '-'.join(selected) or 'detail'
    code = base
    suffix = 2
    while code in used:
        ending = f'-{suffix}'
        code = base[: 24 - len(ending)].rstrip('-') + ending
        suffix += 1
    used.add(code)
    return code


def styled_detail(detail: str, identifiers: Iterable[str]) -> Text:
    rendered = Text(detail)
    for identifier in sorted(set(identifiers), key=len, reverse=True):
        pattern = rf'(?<![\w/.-]){re.escape(identifier)}(?![\w/.-])'
        for match in re.finditer(pattern, detail):
            rendered.stylize(IDENTIFIER_STYLE, match.start(), match.end())
    return rendered


def output_mode(no_color: bool) -> tuple[bool, bool, bool]:
    interactive = (
        sys.stdout.isatty() and not is_clanker() and not os.environ.get('CI') and os.environ.get('TERM') != 'dumb'
    )
    force_color = os.environ.get('FORCE_COLOR')
    forced = is_color_forced(('FORCE_COLOR',))
    styled = forced or (interactive and force_color != '0' and not no_color and 'NO_COLOR' not in os.environ)
    return interactive, styled, forced


def version_parts(row: Result) -> tuple[str, str]:
    current = display_version(row.provider, row.current)
    latest = display_version(row.provider, row.latest) if row.latest and row.latest != row.current else ''
    return current, latest


def version_label(row: Result) -> str:
    current, latest = version_parts(row)
    return f'{current} → {latest}' if current and latest else latest or current


def single_line(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def version_cell(row: Result, links: bool) -> Text:
    current, latest = version_parts(row)
    rendered = Text()
    current_url = row.current_url or (row.version_url if not latest else '')
    if current:
        style = Style(color='cyan', underline=True, link=current_url) if links and current_url else None
        _ = rendered.append(current, style=style)
    if latest:
        if current:
            _ = rendered.append(' → ')
        style = Style(color='cyan', underline=True, link=row.version_url) if links and row.version_url else None
        _ = rendered.append(latest, style=style)
    return rendered


def finding_notes(results: Sequence[Result]) -> tuple[dict[str, str], dict[str, set[str]]]:
    notes: dict[str, str] = {}
    identifiers: dict[str, set[str]] = {}
    used: set[str] = set()
    for row in results:
        if row.state == 'up-to-date' or not row.detail:
            continue
        if row.detail not in notes:
            notes[row.detail] = note_code(row.detail, used)
        if row.detail_identifiers:
            identifiers.setdefault(row.detail, set()).update(row.detail_identifiers)
    return notes, identifiers


def report(results: Sequence[Result], json_output: bool, no_color: bool = False) -> int:
    for row in results:
        for field, value in row.json().items():
            setattr(row, field, clean(value))

    state, code = summary(results)
    counts = dict(sorted(Counter(row.state for row in results).items()))
    document = {'schemaVersion': 1, 'state': state, 'counts': counts, 'results': [row.json() for row in results]}

    if json_output:
        print(json.dumps(document, indent=2))
    else:
        notes, identifiers = finding_notes(results)
        interactive, styled, forced = output_mode(no_color)
        counts_label = ', '.join(f'{count} {name}' for name, count in counts.items())
        if not interactive and not forced:
            visible = [row for row in results if row.state != 'up-to-date']
            for row in visible:
                version = version_label(row)
                suffix = f' {version}' if version else ''
                if row.compatible:
                    suffix += f' (compatible {row.compatible})'
                note = f' [{notes[row.detail]}]' if row.detail else ''
                name = display_name(row.provider, row.name)
                print(single_line(f'{row.provider}: {row.state} {name}{suffix}{note}'))
            for detail, note in notes.items():
                print(f'{note}: {single_line(detail)}')
            print()
            print(f'OUTDATED: {state} ({counts_label})')
            print()
            return code
        console = Console(
            file=sys.stdout,
            color_system='standard' if forced else 'auto',
            force_terminal=styled,
            no_color=not styled,
            highlight=False,
            width=None if sys.stdout.isatty() else 120,
        )
        groups: dict[str, list[Result]] = {}
        for row in results:
            groups.setdefault(row.provider, []).append(row)
        for provider, rows in groups.items():
            table = Table(title=provider, box=box.SQUARE, row_styles=['', 'on grey11'])
            table.add_column('State', no_wrap=True, min_width=10)
            table.add_column('Dependency', no_wrap=True, overflow='ellipsis', max_width=64)
            table.add_column('Version', no_wrap=True, overflow='ellipsis', max_width=28)
            table.add_column('Note', no_wrap=True)
            for row in rows:
                note = notes.get(row.detail, '')
                if row.compatible:
                    note = f'{note}; compatible: {row.compatible}'.lstrip('; ')
                table.add_row(
                    Text(row.state, style=STATE_STYLES[row.state]),
                    Text(display_name(provider, row.name)),
                    version_cell(row, interactive and styled),
                    Text(note),
                )
            console.print(table)
        if notes:
            note_table = Table(title='Finding details', box=box.SQUARE, row_styles=['', 'on grey11'])
            note_table.add_column('Code', no_wrap=True)
            note_table.add_column('Meaning', overflow='fold', max_width=96)
            for detail, note_key in notes.items():
                note_table.add_row(Text(note_key), styled_detail(detail, identifiers.get(detail, ())))
            console.print(note_table)
        verdict = Text('OUTDATED: ', style='bold')
        _ = verdict.append(
            state, style={'ERROR': 'bold red', 'OUTDATED': 'bold yellow', 'UP-TO-DATE': 'bold green'}[state]
        )
        _ = verdict.append(f' ({counts_label})', style='bold')
        console.print()
        console.print(verdict)
        console.print()

    return code


def progress_display(console: Console) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn('{task.description}'),
        BarColumn(bar_width=24),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        auto_refresh=True,
        refresh_per_second=8,
        transient=True,
        expand=False,
    )


def run_jobs(
    work: Sequence[Job], concurrency: int, progress: Progress | None = None, task: TaskID | None = None
) -> list[Result]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(guarded, *job): index for index, job in enumerate(work)}
        batches: list[list[Result] | None] = [None] * len(work)

        for future in concurrent.futures.as_completed(futures):
            batches[futures[future]] = future.result()
            if progress is not None and task is not None:
                progress.advance(task)
    return [row for batch in batches if batch is not None for row in batch]


def collect_jobs(config: Inventory, root: Path, client: Client, console: Console | None = None) -> list[Result]:
    progress = progress_display(console) if console is not None else None
    task = progress.add_task('Discovering inputs', total=None) if progress is not None else None
    with progress if progress is not None else contextlib.nullcontext():
        work = jobs(config, root, client)
        if not work:
            raise Failure('No providers or release entries are enabled')
        if progress is not None and task is not None:
            progress.update(task, description='Checking dependencies', total=len(work), completed=0)
        return run_jobs(work, config.get('concurrency', 6), progress, task)


@dataclass
class Options:
    """Online maintenance reports; never used as cached Nix check results."""

    config: Path
    """Nix-generated provider configuration."""
    root: Path | None = None
    """Repository root; otherwise discover the configured marker upward."""
    json: bool = False
    """Emit the versioned JSON report."""
    no_color: bool = False
    """Disable color in the Rich table."""


def main() -> int:
    options = cast(Callable[[type[Options]], Options], tyro.cli)(Options)

    try:
        config = cast(Config, json.loads(options.config.read_text()))
        root = options.root.resolve() if options.root else find_root(Path.cwd(), config['treeRootFile'])
        client = Client(config['timeout'], config['githubApi'], config.get('nvchecker', 'nvchecker'), preflight=True)
        interactive, styled, forced = output_mode(options.no_color)
        live = interactive and styled and not options.json
        progress_console = (
            Console(file=sys.stdout, color_system='standard' if forced else 'auto', force_terminal=styled)
            if live
            else None
        )
        results = collect_jobs(config, root, client, progress_console)
    except Exception as error:  # noqa: BLE001 - preserve other providers and redact raw diagnostics
        detail = str(error) if isinstance(error, Failure) else f'Invalid report configuration ({type(error).__name__})'
        results = [Result('report', 'configuration', '.', 'error', detail=detail)]

    return report(results, options.json, options.no_color)


if __name__ == '__main__':
    sys.exit(main())
