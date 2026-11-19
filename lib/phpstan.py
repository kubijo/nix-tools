"""Run PHPStan with a consumer-selected PHP runtime and native project policy."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast


class Settings(TypedDict):
    root: str
    configFile: str
    php: str
    exe: str
    extraOptions: list[str]


def analysis_options(options: list[str]) -> None:
    # An allowlist also rejects Symfony's abbreviated long options, which could
    # otherwise enable baseline generation or fixing through a shorter spelling.
    values = {'--level', '-l', '--error-format', '--autoload-file', '-a', '--memory-limit'}
    switches = {
        '--no-progress',
        '--debug',
        '--xdebug',
        '--fail-without-result-cache',
        '--ansi',
        '--no-ansi',
        '--quiet',
        '-q',
        '--verbose',
        '-v',
        '-vv',
        '-vvv',
        '--no-interaction',
        '-n',
    }
    index = 0
    while index < len(options):
        option = options[index]
        flag, equals, value = option.partition('=')
        if flag in values:
            if not equals:
                index += 1
                value = options[index] if index < len(options) else ''
            if not value or value.startswith('-'):
                raise ValueError(f'{flag}: an explicit option value is required')
        elif option not in switches:
            raise ValueError(
                f'{option!r}: unsupported analysis option; use configFile for configuration and source paths; '
                + 'fixing, baseline generation and non-analysis modes are not allowed'
            )
        index += 1


def run(settings: Settings) -> int:
    repository = Path.cwd().resolve()
    project = (repository / settings['root']).resolve(strict=True)
    if not project.is_relative_to(repository) or not project.is_dir():
        raise ValueError('project root must be a directory inside the repository')
    config = (project / settings['configFile']).resolve(strict=True)
    if not config.is_relative_to(project) or not config.is_file():
        raise ValueError('configFile must be a file inside the project root')
    php = Path(settings['php'])
    if not php.is_file() or not os.access(php, os.X_OK):
        raise ValueError(f'PHP interpreter is missing or not executable: {php}')
    analysis_options(settings['extraOptions'])

    env = os.environ.copy()
    # The selected Nix PHP wrapper supplies its own ini and extension policy.
    for key in ('PHPRC', 'PHP_INI_SCAN_DIR'):
        _ = env.pop(key, None)
    env['PATH'] = os.pathsep.join([str(php.parent), *filter(None, [env.get('PATH', '')])])
    env['CI'] = '1'
    return subprocess.run(
        [settings['exe'], 'analyse', '--configuration', str(config), '--no-progress', *settings['extraOptions']],
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
        print(f'phpstan: {error}', file=sys.stderr)
        status = 1
    sys.exit(status if status >= 0 else 1)
