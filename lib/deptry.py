"""Run deptry once per project."""

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict, cast


class Settings(TypedDict):
    root: str
    sourceRoots: list[str]
    configFile: str
    extraOptions: list[str]
    exe: str


class Policy(TypedDict, total=False):
    experimental_namespace_package: bool
    known_first_party: list[str]


class Manifest(TypedDict, total=False):
    tool: dict[str, Policy]


def directory(base: Path, value: str, label: str) -> Path:
    path = (base / value).resolve(strict=True)
    if not path.is_relative_to(base):
        raise ValueError(f'{label} escapes {base}: {value}')
    if not path.is_dir():
        raise ValueError(f'{label} is not a directory: {value}')
    return path


def local_modules(root: Path, namespace: bool) -> Iterator[str]:
    # Match deptry discovery for collapsed roots; namespaces need a descendant .py file.
    for child in root.iterdir():
        if child.is_file() and child.suffix == '.py' and child.name != '__init__.py':
            yield child.stem
        elif child.is_dir():
            if namespace:

                def onerror(error: OSError) -> None:
                    raise error

                found = any(name.endswith('.py') for _, _, files in os.walk(child, onerror=onerror) for name in files)
            else:
                found = any(path.suffix == '.py' for path in child.iterdir())
            if found:
                yield child.stem


def run(settings: Settings) -> int:
    project = directory(Path.cwd().resolve(), settings['root'], 'project root')
    roots = list(dict.fromkeys(directory(project, value, 'source root') for value in settings['sourceRoots']))
    manifest = (project / settings['configFile']).resolve(strict=True)
    if not manifest.is_file():
        raise ValueError(f'configFile is not a manifest file: {manifest}')
    with manifest.open('rb') as stream:
        config = cast(Manifest, cast(object, tomllib.load(stream))).get('tool', {}).get('deptry', {})

    options = settings['extraOptions']
    for option in options:
        if option.split('=', 1)[0] in {'--config', '--known-first-party', '-kf', '--', '--help', '--version'}:
            raise ValueError(f'{option}: use configFile or [tool.deptry] for configuration and sourceRoots for targets')

    scan_roots = [root for root in roots if not any(parent != root and root.is_relative_to(parent) for parent in roots)]
    namespace = config.get('experimental_namespace_package', False) or '--experimental-namespace-package' in options
    inferred = {module for root in roots if root not in scan_roots for module in local_modules(root, namespace)}
    discovery: list[str] = []
    if inferred:
        # CLI values replace TOML lists: preserve explicit first-party modules.
        for module in sorted(inferred | set(config.get('known_first_party', []))):
            discovery.extend(['--known-first-party', module])

    env = os.environ.copy()
    # Prevent ambient Python metadata from affecting the check.
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        _ = env.pop(key, None)
    env['PYTHONNOUSERSITE'] = '1'
    color = 'NO_COLOR' not in env and (
        sys.stderr.isatty() or any(env.get(key, '0') not in ('', '0') for key in ('FORCE_COLOR', 'CLICOLOR_FORCE'))
    )
    return subprocess.run(
        [
            settings['exe'],
            '--config',
            str(manifest),
            *([] if color else ['--no-ansi']),
            *options,
            *discovery,
            '--',
            *(str(root.relative_to(project)) for root in scan_roots),
        ],
        cwd=project,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode


if __name__ == '__main__':
    try:
        with open(sys.argv[1]) as stream:
            status = run(cast(Settings, json.load(stream)))
    except (OSError, ValueError) as error:
        print(f'deptry: {error}', file=sys.stderr)
        status = 1
    sys.exit(status if status >= 0 else 1)
