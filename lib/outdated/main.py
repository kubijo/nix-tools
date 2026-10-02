"""Online maintenance reports; never used as cached Nix check results."""

import concurrent.futures
import json
import os
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass

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
from sources import nix_jobs, release_entry, workflow_jobs


def clean(value):
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


def guarded(provider, name, source, callback):
    rows = []

    try:
        value = callback()
        for result in [value] if isinstance(value, Result) else value:
            if (
                not isinstance(result, Result)
                or result.state not in STATES
                or not all(isinstance(value, str) for value in result.json().values())
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


def jobs(config, root, client):
    providers = config['providers']
    output = []

    def add(provider, name, source, callback):
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
                callback = PROVIDERS[provider]
                add(
                    provider,
                    provider,
                    source,
                    lambda callback=callback, settings=settings, directory=directory: callback(
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
        add('skips', item['name'], item['source'], lambda item=item: release_entry(client, item, 'skips'))

    for adapter in config['adapters']:

        def execute(adapter=adapter):
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


def find_root(start, marker):
    current = start.resolve()
    while not (current / marker).exists():
        if current.parent == current:
            raise Failure(f'Could not find repository marker {marker}')
        current = current.parent

    return current


def report(results, json_output, no_color=False):
    for row in results:
        for field, value in row.json().items():
            setattr(row, field, clean(value))

    state, code = summary(results)
    counts = dict(sorted(Counter(row.state for row in results).items()))
    document = {'schemaVersion': 1, 'state': state, 'counts': counts, 'results': [row.json() for row in results]}

    if json_output:
        print(json.dumps(document, indent=2))
    else:
        previous = None
        color = sys.stdout.isatty() and not no_color and 'NO_COLOR' not in os.environ
        for row in results:
            if row.provider != previous:
                print(f'\n{row.provider}')
                previous = row.provider
            change = f' {row.current} -> {row.latest}' if row.latest else (f' {row.current}' if row.current else '')
            line = f'  {row.state:12} {row.name}:{change}'
            print(f'\x1b[90m{line}\x1b[0m' if color and row.state == 'unknown' else line)
            if row.compatible:
                print(f'               compatible: {row.compatible}')
            if row.detail:
                print('               ' + row.detail.replace('\n', '\n               '))
        print(f'\nOUTDATED: {state} ({", ".join(f"{v} {k}" for k, v in counts.items())})')

    return code


@dataclass
class Options:
    """Online maintenance reports; never used as cached Nix check results."""

    config: pathlib.Path
    """Nix-generated provider configuration."""
    root: pathlib.Path | None = None
    """Repository root; otherwise discover the configured marker upward."""
    json: bool = False
    """Emit the versioned JSON report."""
    no_color: bool = False
    """Plain output (also the default)."""


def main():
    options = tyro.cli(Options)

    try:
        config = json.loads(options.config.read_text())
        root = options.root.resolve() if options.root else find_root(pathlib.Path.cwd(), config['treeRootFile'])
        client = Client(config['timeout'], config['githubApi'], config.get('nvchecker', 'nvchecker'))
        work = jobs(config, root, client)
        if not work:
            raise Failure('No providers or release entries are enabled')
        with concurrent.futures.ThreadPoolExecutor(max_workers=config['concurrency']) as pool:
            futures = [pool.submit(guarded, *job) for job in work]
            results = [row for future in futures for row in future.result()]
    except Exception as error:  # noqa: BLE001 - preserve other providers and redact raw diagnostics
        detail = str(error) if isinstance(error, Failure) else f'Invalid report configuration ({type(error).__name__})'
        results = [Result('report', 'configuration', '.', 'error', detail=detail)]

    return report(results, options.json, options.no_color)


if __name__ == '__main__':
    sys.exit(main())
