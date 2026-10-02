"""Run djLint with declared policy and scope, isolated from ambient configuration."""

import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib

# Accept only style and rule switches: discovery, mode, external configuration
# and success-suppression switches belong to nix-tools, never to the native invocation.
FLAGS = {
    'preserve-leading-space',
    'preserve-blank-lines',
    'preserve-class-newlines',
    'format-css',
    'format-js',
    'ignore-case',
    'line-break-after-multiline-tag',
    'format-attribute-template-tags',
    'single-attribute-per-line',
    'format-attribute-js-json',
    'close-void-tags',
    'no-line-after-yaml',
    'no-function-formatting',
    'no-set-formatting',
}
VALUES = {
    'ignore',
    'include',
    'indent',
    'ignore-blocks',
    'custom-blocks',
    'blank-line-after-tag',
    'blank-line-before-tag',
    'custom-html',
    'max-line-length',
    'max-attribute-length',
    'format-attribute-js-json-pattern',
    'format-attribute-js-json-min-props',
    'indent-css',
    'indent-js',
    'max-blank-lines',
}
CONFIG_KEYS = {key.replace('-', '_') for key in FLAGS | (VALUES - {'indent-css', 'indent-js'})} | {
    'profile',
    'per-file-ignores',
    'js',
    'css',
}


def native_options(options):
    index = 0
    while index < len(options):
        argument = options[index]
        flag, equals, value = argument.partition('=')
        key = flag.removeprefix('--') if flag.startswith('--') else ''

        if key in FLAGS and not equals:
            index += 1

        elif key in VALUES:
            if not equals:
                index += 1
                if index == len(options) or options[index].startswith('-'):
                    raise ValueError(f'missing value for {flag}')
            elif not value:
                raise ValueError(f'missing value for {flag}')
            index += 1

        else:
            raise ValueError(
                f'unsupported djLint option {argument!r}; use includes/exclude, configFile and the language entry for scope, policy and mode'
            )

    return options


def load_policy(policy, syntax):
    content = pathlib.Path(policy).read_text(encoding='utf-8')
    settings = json.loads(content) if syntax == 'json' else tomllib.loads(content)

    if syntax == 'pyproject':
        tool_settings = settings.get('tool')
        if not isinstance(tool_settings, dict) or not isinstance(tool_settings.get('djlint'), dict):
            raise ValueError('pyproject.toml must contain a [tool.djlint] table')
        settings = tool_settings['djlint']

    if not isinstance(settings, dict):
        raise TypeError('djLint config must be an object/table')

    unknown = settings.keys() - CONFIG_KEYS
    if unknown:
        raise ValueError(
            f'unsupported djLint config keys: {sorted(unknown)}; file selection and execution mode are controlled by nix-tools'
        )

    for flag in FLAGS:
        key = flag.replace('-', '_')
        if key in settings and type(settings[key]) is not bool:
            raise ValueError(f'djLint config {key} must be a boolean')

    # Native djLint prints an error then uses defaults for invalid integers.
    for key in (
        'indent',
        'max_line_length',
        'max_attribute_length',
        'max_blank_lines',
        'format_attribute_js_json_min_props',
    ):
        if key in settings and (type(settings[key]) is not int or settings[key] < 0):
            raise ValueError(f'djLint config {key} must be a nonnegative integer')
    return settings


def main():
    tool, mode, profile, policy, syntax, option_count, *arguments = sys.argv[1:]

    if mode not in ('format', 'lint'):
        raise ValueError('djLint mode must be format or lint')

    boundary = int(option_count)
    if boundary < 0 or arguments[boundary : boundary + 1] != ['--']:
        raise ValueError('invalid djLint option count or file boundary')

    options = native_options(arguments[:boundary])
    if profile == 'nunjucks':
        # Native expression formatting interprets literals as JSON/Python, which
        # changes Nunjucks escapes. Enforce this even with a custom policy.
        options += ['--no-set-formatting', '--no-function-formatting']

    files = arguments[boundary + 1 :]
    settings = load_policy(policy, syntax)
    status = 0

    with tempfile.TemporaryDirectory(prefix='nix-tools-djlint-') as directory:
        root = pathlib.Path(directory)
        # Canonical JSON also avoids native config parse failures being swallowed.
        (root / '.djlintrc').write_text(json.dumps(settings), encoding='utf-8')
        (root / '.git').mkdir()
        for filename in files:
            source = pathlib.Path(filename)
            relative = source.absolute().relative_to(pathlib.Path.cwd()).as_posix()
            if (
                '..' in pathlib.Path(relative).parts
                or source.is_symlink()
                or not source.resolve().is_relative_to(pathlib.Path.cwd())
            ):
                raise ValueError(f'unsafe djLint input: {filename}')
            original = source.read_bytes()
            result = subprocess.run(
                [
                    tool,
                    '-',
                    '--reformat' if mode == 'format' else '--lint',
                    '--profile',
                    profile,
                    '--stdin-filename',
                    relative,
                    '--no-github-output',
                    *options,
                ],
                input=original,
                capture_output=True,
                cwd=root,
                check=False,
            )

            if result.returncode or result.stderr or (mode == 'format' and original and not result.stdout):
                print(f'djLint ({profile}): {filename}', file=sys.stderr)
                if mode == 'format' and original and not result.stdout:
                    print('formatter returned empty output; source preserved', file=sys.stderr)
                sys.stderr.buffer.write(result.stderr)
                sys.stderr.buffer.write(result.stdout)
                status = 1

            elif mode == 'format' and result.stdout != original:
                source.write_bytes(result.stdout)

    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, TypeError, OSError) as error:
        sys.exit(f'djLint: {error}')
