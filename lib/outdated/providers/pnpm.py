"""Native pnpm dependency report."""

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from common import Failure, Result, command_json, records, relative, snapshot, validate_adapter

from .contract import ProviderConfig


class Project(TypedDict):
    path: str
    name: NotRequired[str]
    dependencies: NotRequired[dict[str, dict[str, str]]]
    devDependencies: NotRequired[dict[str, dict[str, str]]]
    optionalDependencies: NotRequired[dict[str, dict[str, str]]]


class Update(TypedDict):
    name: str
    current: str
    spec: str
    latest: NotRequired[str]
    compatible: NotRequired[str]


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    _ = relative(root, 'package.json')
    _ = relative(root, 'pnpm-lock.yaml')
    with tempfile.TemporaryDirectory(prefix='nix-tools-pnpm-cache-') as cache, snapshot(root) as work:
        env = {'XDG_CACHE_HOME': cache}
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
                env=env,
            )
        )
        if not projects:
            raise Failure('pnpm returned no workspace inventory')
        for record in projects:
            project = cast(Project, cast(object, record))
            path = Path(project['path'])
            if not path.resolve().is_relative_to(work):
                raise Failure('pnpm project escaped the disposable workspace')
            source = 'pnpm-lock.yaml:' + path.relative_to(work).as_posix()
            groups: tuple[Literal['dependencies', 'devDependencies', 'optionalDependencies'], ...] = (
                'dependencies',
                'devDependencies',
                'optionalDependencies',
            )
            manifest = command_json(
                [config['exe'], 'pkg', 'get', *groups, '--json', '--dir', str(path)], path, timeout, env=env
            )
            if not isinstance(manifest, dict):
                raise Failure('Unsupported pnpm manifest report')
            manifest = cast(dict[str, dict[str, str]], manifest)
            if any(set(manifest.get(group, {})) - set(project.get(group, {})) for group in groups):
                raise Failure('pnpm lock inventory omitted declared dependencies')
            updates: list[Update] = []
            count = 0
            for group in groups:
                for name, item in cast(dict[str, dict[str, str]], project.get(group, {})).items():
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
                        env=env,
                    )
                    if (
                        not isinstance(data, dict)
                        or len(cast(dict[str, object], data)) > 1
                        or any(not isinstance(v, dict) for v in cast(dict[str, object], data).values())
                    ):
                        raise Failure('Unsupported pnpm outdated JSON schema')
                    update: Update = {'name': name, 'current': current, 'spec': spec}
                    if data:
                        candidate = next(iter(cast(dict[str, dict[str, str]], data).values()))
                        if candidate['current'] != current:
                            raise Failure('pnpm inventory and update report disagree')
                        update['latest'] = candidate['latest']
                        update['compatible'] = candidate.get('wanted', '')
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
