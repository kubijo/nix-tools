"""Native cargo dependency report."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast

from common import Result, command_json, external, records, relative, snapshot

from .contract import ProviderConfig


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    _ = relative(root, 'Cargo.toml')
    _ = relative(root, 'Cargo.lock')
    with snapshot(root) as work:
        inventory = command_json(
            [config['cargo'], 'metadata', '--locked', '--format-version=1', '--all-features'],
            work,
            timeout,
        )
        data = command_json([config['exe'], 'outdated', '--format=json', '--workspace', '--exit-code=0'], work, timeout)
    documents = cast(list[object], data) if isinstance(data, list) else [data]
    count = 0
    for document in documents:
        for record in records(document, 'dependencies'):
            item = cast(dict[str, str], record)
            count += 1
            current, latest = item['project'], item['latest']
            name = item['name'] + (f' ({item["platform"]})' if item.get('platform') else '')
            if latest in {'Removed', '---'} or current == '---':
                yield external(
                    'cargo', name, 'Cargo.lock', current, 'Resolution changed the dependency graph; review manually'
                )
            else:
                # cargo-outdated owns version ordering and selects these candidates.
                yield Result(
                    'cargo',
                    name,
                    'Cargo.lock',
                    'up-to-date' if current == latest else 'outdated',
                    current,
                    '' if item['compat'] == '---' else item['compat'],
                    latest,
                    'Cargo requirement-compatible candidate is separate from latest upstream',
                )
    for record in records(inventory, 'packages'):
        package = cast(dict[str, str | None], record)
        if (package.get('source') or '').startswith('git+'):
            yield external(
                'cargo',
                cast(str, package['name']),
                'Cargo.lock',
                package['version'],
                'Git dependency; configure a release entry to track upstream releases',
            )
    if not count:
        yield Result(
            'cargo', 'workspace', 'Cargo.lock', 'up-to-date', detail='Native workspace report found no registry updates'
        )
