"""Native pnpm dependency report."""

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

from common import Failure, Result, command_json, records, relative, snapshot, validate_adapter

from .contract import ProviderConfig


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    relative(root, 'package.json')
    relative(root, 'pnpm-lock.yaml')
    with snapshot(root) as work:
        projects = records(
            command_json(
                [
                    config['exe'],
                    'list',
                    '--lockfile-only',
                    '--recursive',
                    '--include-workspace-root',
                    '--json',
                    '--depth',
                    '0',
                ],
                work,
                timeout,
            )
        )
        if not projects:
            raise Failure('pnpm returned no workspace inventory')
        for project in projects:
            path = Path(project['path'])
            if not path.resolve().is_relative_to(work):
                raise Failure('pnpm project escaped the disposable workspace')
            source = 'pnpm-lock.yaml:' + path.relative_to(work).as_posix()
            groups = ('dependencies', 'devDependencies', 'optionalDependencies')
            manifest = command_json([config['exe'], 'pkg', 'get', *groups, '--json', '--dir', str(path)], path, timeout)
            if not isinstance(manifest, dict):
                raise Failure('Unsupported pnpm manifest report')
            if any(set(manifest.get(group, {})) - set(project.get(group, {})) for group in groups):
                raise Failure('pnpm lock inventory omitted declared dependencies')
            updates = []
            count = 0
            for group in groups:
                for name, item in project.get(group, {}).items():
                    count += 1
                    current = item['version']
                    if current.startswith(('link:', 'workspace:')):
                        yield Result('pnpm', name, source, 'skipped', current, detail='Local workspace dependency')
                        continue
                    spec = manifest.get(group, {}).get(name)
                    if not isinstance(spec, str):
                        raise Failure('pnpm inventory and manifest disagree')
                    # Query each declared name, not a merged name-keyed report:
                    # aliases and distinct workspace versions can otherwise collapse.
                    data = command_json(
                        [config['exe'], 'outdated', name, '--format', 'json', '--dir', str(path)],
                        path,
                        timeout,
                        codes=(0, 1),
                    )
                    if (
                        not isinstance(data, dict)
                        or len(data) > 1
                        or any(not isinstance(v, dict) for v in data.values())
                    ):
                        raise Failure('Unsupported pnpm outdated JSON schema')
                    update = {'name': name, 'current': current, 'spec': spec}
                    if data:
                        candidate = next(iter(data.values()))
                        if candidate['current'] != current:
                            raise Failure('pnpm inventory and update report disagree')
                        update |= {'latest': candidate['latest'], 'compatible': candidate.get('wanted', '')}
                    updates.append(update)
            if updates:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as report:
                    json.dump(updates, report)
                    report.flush()
                    document = command_json([config['semver'], 'versions', report.name], work, timeout)
                yield from validate_adapter(document, 'pnpm', source)
            if not count:
                yield Result(
                    'pnpm', project.get('name', '.'), source, 'up-to-date', detail='Project has no direct dependencies'
                )
