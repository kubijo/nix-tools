"""Native npm dependency report."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from common import Failure, Result, command_json, records, relative, snapshot, validate_adapter

from .contract import ProviderConfig


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    _ = relative(root, 'package.json')
    _ = relative(root, 'package-lock.json')
    executable = shutil.which(config['exe'])
    if executable is None:
        raise Failure('Configured npm executable is missing')
    with snapshot(root) as work:
        document = command_json([config['reporter'], 'npm', executable], work, timeout)
    if document == {'schemaVersion': 1, 'results': []}:
        yield Result('npm', 'project', 'package-lock.json', 'up-to-date', detail='Lockfile contains no dependencies')
        return
    for record in records(document, 'results'):
        item = record.copy()
        source = item.pop('source')
        if not isinstance(source, str):
            raise Failure('Unsupported npm source field')
        yield from validate_adapter(
            {'schemaVersion': cast(dict[str, object], document)['schemaVersion'], 'results': [item]}, 'npm', source
        )
