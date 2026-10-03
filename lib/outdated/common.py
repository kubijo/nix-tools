"""Shared result protocol and read-only provider execution."""

import contextlib
import dataclasses
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile

from packaging.version import InvalidVersion, Version

STATES = {'up-to-date', 'outdated', 'ahead', 'pinned', 'skipped', 'unknown', 'blocked', 'error'}


class Failure(Exception):
    """A diagnostic safe to include in a report (never raw client stderr)."""


class UnreadableSource(Failure):
    """A successful lookup without a usable version, rather than a lookup failure."""


@dataclasses.dataclass
class Result:
    provider: str
    name: str
    source: str
    state: str
    current: str = ''
    compatible: str = ''
    latest: str = ''
    detail: str = ''
    version_url: str = dataclasses.field(default='', repr=False, compare=False)
    current_url: str = dataclasses.field(default='', repr=False, compare=False)
    detail_identifiers: tuple[str, ...] = dataclasses.field(default=(), repr=False, compare=False)

    def json(self):
        return {
            'provider': self.provider,
            'name': self.name,
            'source': self.source,
            'state': self.state,
            'current': self.current,
            'compatible': self.compatible,
            'latest': self.latest,
            'detail': self.detail,
        }


def version(value):
    try:
        return Version(value)
    except InvalidVersion:
        return None


def compare(provider, name, source, current, latest, compatible='', detail=''):
    current, latest = str(current), str(latest)
    if not current or not latest:
        raise Failure('Version lookup returned an empty version')

    before, after = version(current), version(latest)

    if current == latest:
        state = 'up-to-date'

    elif before is not None and after is not None:
        state = 'ahead' if before > after else ('up-to-date' if before == after else 'outdated')

    elif '-unstable-' in current and (baseline := version(current.split('-unstable-')[0])) is not None and after:
        state = 'ahead' if baseline >= after else 'outdated'

    else:
        state = 'unknown'
        detail = (detail + '; version ordering is not known').lstrip('; ')

    return Result(provider, name, source, state, current, str(compatible or ''), latest, detail)


def summary(results):
    if not results or any(row.state in {'error', 'unknown', 'blocked'} for row in results):
        return 'ERROR', 2

    if any(row.state == 'outdated' for row in results):
        return 'OUTDATED', 1

    return 'UP-TO-DATE', 0


def relative(root, value, *, directory=False):
    path = pathlib.Path(value)

    if path.is_absolute() or '..' in path.parts:
        raise Failure('Expected a repository-relative path without parent traversal')

    target = (root / path).resolve()

    if not target.is_relative_to(root.resolve()):
        raise Failure('Input path escapes the repository')

    if not (target.is_dir() if directory else target.is_file()):
        raise Failure(f'Required {"directory" if directory else "file"} is missing: {value}')

    return target


def environment():
    return os.environ | {
        'NO_COLOR': '1',
        'FORCE_COLOR': '0',
        'TERM': 'dumb',
        'CI': '1',
        'GIT_TERMINAL_PROMPT': '0',
        'GH_PROMPT_DISABLED': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'UV_PYTHON_DOWNLOADS': 'never',
        'UV_NO_MANAGED_PYTHON': '1',
        'npm_config_ignore_scripts': 'true',
        'YARN_ENABLE_SCRIPTS': 'false',
        'YARN_ENABLE_TELEMETRY': '0',
        'YARN_ENABLE_IMMUTABLE_INSTALLS': 'true',
        'COMPOSER_NO_INTERACTION': '1',
    }


def run(command, root, timeout, *, codes=(0,), env=None):
    with subprocess.Popen(
        command,
        cwd=root,
        env=environment() | (env or {}),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise Failure('Provider command timed out') from None

    if process.returncode not in codes:
        raise Failure(
            f'Provider command failed (exit {process.returncode}); diagnostics withheld to protect credentials'
        )

    return stdout.decode('utf-8'), stderr.decode('utf-8')


def command_json(command, root, timeout, **kwargs):
    output, _ = run(command, root, timeout, **kwargs)

    try:
        return json.loads(output)
    except ValueError, TypeError:
        raise Failure('Provider returned malformed JSON') from None


@contextlib.contextmanager
def snapshot(root):
    # Native package managers may refresh metadata in the project even in report
    # mode. Give them a copy and never a writable link back to the consumer.
    ignored = {'.git', '.venv', 'node_modules', 'target', '.tmp', '__pycache__', 'result'}
    with tempfile.TemporaryDirectory(prefix='nix-tools-outdated-') as directory:
        target = pathlib.Path(directory) / 'project'

        def ignore(path, names):
            skipped = {name for name in names if name in ignored or name.startswith('result-')}
            for name in set(names) - skipped:
                file = pathlib.Path(path) / name
                if file.is_symlink():
                    resolved = file.resolve()
                    if not resolved.is_relative_to(root.resolve()):
                        raise Failure('Project contains an external symlink; choose a self-contained root')
                    if resolved.is_dir() and any(parent.resolve() == resolved for parent in file.parents):
                        raise Failure('Project contains a directory symlink cycle')
            return skipped

        shutil.copytree(root, target, symlinks=False, ignore=ignore)
        yield target


def validate_adapter(document, provider, source):
    if (
        not isinstance(document, dict)
        or type(document.get('schemaVersion')) is not int
        or document.get('schemaVersion') != 1
        or not isinstance(document.get('results'), list)
    ):
        raise Failure('Adapter must return schemaVersion=1 and a results array')
    if not document['results']:
        raise Failure('Adapter returned an empty report')

    output = []
    fields = {'name', 'state', 'current', 'compatible', 'latest', 'detail'}

    for item in document['results']:
        if not isinstance(item, dict) or item.keys() - fields:
            raise Failure('Adapter returned unsupported result fields')
        if (
            not all(isinstance(v, str) for v in item.values())
            or not item.get('name')
            or item.get('state') not in STATES
        ):
            raise Failure('Adapter returned an invalid result')
        output.append(Result(provider, source=source, **item))

    return output


def external(provider, name, source, current, detail):
    return Result(provider, name, source, 'unknown', str(current), detail=detail)


def records(value, key=None):
    result = value[key] if key else value
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise Failure('Unexpected native result schema')
    return result
