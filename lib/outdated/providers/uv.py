"""Native uv dependency report."""

from collections.abc import Iterator
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from common import Failure, Result, command_json, compare, external, relative, snapshot
from sources import registry_url

from .contract import ProviderConfig


class Package(TypedDict):
    kind: str
    name: str
    source: dict[str, object]
    version: NotRequired[str]
    latest_version: NotRequired[str]


class Document(TypedDict):
    schema: dict[str, str]
    resolution: dict[str, Package]


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    _ = relative(root, 'pyproject.toml')
    _ = relative(root, 'uv.lock')
    with snapshot(root) as work:
        data = command_json(
            [
                config['exe'],
                'tree',
                '--locked',
                '--universal',
                '--outdated',
                '--format',
                'json',
                '--all-groups',
                '--no-python-downloads',
                '--no-cache',
            ],
            work,
            timeout,
        )
    fields = cast(dict[str, object], data)
    schema = cast(dict[str, object], fields.get('schema', {}))
    if schema.get('version') != 'preview' or not isinstance(fields.get('resolution'), dict):
        raise Failure('Unsupported UV tree JSON schema')
    document = cast(Document, data)
    count = 0
    for item in document['resolution'].values():
        if item['kind'] != 'package':
            continue
        count += 1
        name, current, source = item['name'], item.get('version', ''), item['source']
        if set(source) & {'virtual', 'editable'}:
            yield Result('uv', name, 'uv.lock', 'skipped', current, detail='Local/workspace package')
        elif 'registry' in source:
            latest = item.get('latest_version')
            if not latest:
                yield Result(
                    'uv',
                    name,
                    'uv.lock',
                    'unknown',
                    current,
                    detail='UV did not report a version; current and failed registry access cannot be distinguished',
                )
                continue
            row = compare(
                'uv',
                name,
                'uv.lock',
                current,
                latest,
                detail='Upstream availability; not a manifest-compatible resolution',
            )
            registry = source['registry']
            if (
                isinstance(registry, dict)
                and cast(dict[str, str], registry).get('url', '').rstrip('/') == 'https://pypi.org/simple'
            ):
                row.version_url = registry_url('pypi', name, latest)
                row.current_url = registry_url('pypi', name, current)
            yield row
        else:
            yield external(
                'uv', name, 'uv.lock', current, 'No registry candidate; configure an explicit release policy'
            )
    if not count:
        raise Failure('UV returned no package inventory')
