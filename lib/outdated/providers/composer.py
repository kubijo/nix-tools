"""Native composer dependency report."""

from collections.abc import Iterator
from pathlib import Path

from common import Result, command_json, external, records, relative, snapshot

from .contract import ProviderConfig


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    relative(root, 'composer.json')
    relative(root, 'composer.lock')
    with snapshot(root) as work:
        data = command_json(
            [
                config['exe'],
                '--no-plugins',
                '--no-scripts',
                '--no-interaction',
                'outdated',
                '--locked',
                '--all',
                '--format=json',
            ],
            work,
            timeout,
        )
    packages = records(data, 'locked')
    states = {'up-to-date': 'up-to-date', 'semver-safe-update': 'outdated', 'update-possible': 'outdated'}
    for item in packages:
        current = item['version']
        if current.startswith('dev-') or '-dev' in current:
            yield external(
                'composer',
                item['name'],
                'composer.lock',
                current,
                'Development dependency needs an explicit release policy',
            )
        elif item.get('latest-status') not in states or not item.get('latest'):
            yield external('composer', item['name'], 'composer.lock', current, 'No native release verdict')
        else:
            yield Result(
                'composer',
                item['name'],
                'composer.lock',
                states[item['latest-status']],
                current,
                latest=item['latest'],
                detail='Composer latest candidate respects its platform policy',
            )
    if not packages:
        yield Result('composer', 'project', 'composer.lock', 'up-to-date', detail='Lockfile contains no packages')
