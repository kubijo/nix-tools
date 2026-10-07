"""Native yarn dependency report."""

from collections.abc import Iterator
from pathlib import Path

from common import Result, command_json, relative, run, snapshot, validate_adapter

from .contract import ProviderConfig


def report(config: ProviderConfig, root: Path, timeout: float) -> Iterator[Result]:
    _ = relative(root, 'package.json')
    _ = relative(root, 'yarn.lock')
    plugin = Path(__file__).parent.parent / 'yarn-report.cjs'
    with snapshot(root) as work:
        _ = run([config['exe'], 'plugin', 'import', str(plugin)], work, timeout, env={'YARN_IGNORE_PATH': '1'})
        document = command_json([config['exe'], 'nix-tools-outdated'], work, timeout, env={'YARN_IGNORE_PATH': '1'})
    yield from validate_adapter(document, 'yarn', 'yarn.lock')
