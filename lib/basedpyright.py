"""Run basedpyright with explicit project configuration."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict, cast


class Settings(TypedDict):
    configFile: str
    python: str
    exe: str
    reporter: str


def run(settings: Settings) -> int:
    root = Path.cwd().resolve()
    try:
        config = (root / settings['configFile']).resolve(strict=True)
    except OSError as error:
        raise ValueError(f'cannot read configFile {settings["configFile"]!r}: {error}') from error
    if not config.is_relative_to(root) or not config.is_file() or config.name != 'pyproject.toml':
        raise ValueError('configFile must be a pyproject.toml file inside the repository')
    with config.open('rb') as stream:
        document = cast(dict[str, object], tomllib.load(stream))
    tool = document.get('tool')
    policy = cast(dict[str, object], tool).get('basedpyright') if isinstance(tool, dict) else None
    if not isinstance(policy, dict) or not policy:
        raise ValueError(f'{config}: provide a nonempty [tool.basedpyright] configuration')

    python = Path(settings['python'])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(f'Python interpreter is missing or not executable: {python}')
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        _ = env.pop(key, None)
    env['PYTHONNOUSERSITE'] = '1'
    env['CI'] = '1'
    command = [settings['exe'], '--project', str(config), '--pythonpath', str(python)]
    if settings['reporter'] == 'rich':
        # -I omits the script directory; restore it for packaged imports.
        sys.path.insert(0, str(Path(__file__).parent))
        from basedpyright_report import Report, render

        result = subprocess.run(
            [*command, '--outputjson'],
            cwd=config.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.stdout.strip() or result.returncode == 0:
            try:
                render(cast(Report, json.loads(result.stdout)), root)
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                print(f'basedpyright-report: invalid checker output: {error}', file=sys.stderr)
                return result.returncode or 1
        return result.returncode
    return subprocess.run(
        command,
        cwd=config.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode


if __name__ == '__main__':
    try:
        with open(sys.argv[1]) as stream:
            status = run(cast(Settings, json.load(stream)))
    except (OSError, ValueError) as error:
        print(f'basedpyright: {error}', file=sys.stderr)
        status = 1
    sys.exit(status if status >= 0 else 1)
