"""Apply an explicit EditorConfig policy; only native checker --fix changes bytes."""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

import editorconfig


def main() -> int:
    tool, mode, policy, *arguments = sys.argv[1:]
    if mode not in ('check', 'fix'):
        raise ValueError('whitespace mode must be check or fix')
    # Both runners separate native options from the selected files.
    boundary = arguments.index('--')
    options, files = arguments[:boundary], arguments[boundary + 1 :]
    if any(option.split('=', 1)[0] in ('--config', '-config') for option in options):
        raise ValueError('whitespace: use configFile for an explicit EditorConfig policy')
    if any(option.split('=', 1)[0] in ('--exclude', '-exclude') for option in options):
        raise ValueError('whitespace: use the exclude option instead of native --exclude flags; paths are staged')
    if mode == 'check' and any(option.split('=', 1)[0] in ('--fix', '-fix') for option in options):
        raise ValueError('whitespace: fixing flags are not allowed in check mode; use format.whitespace')
    status = 0
    with tempfile.TemporaryDirectory(prefix='nix-tools-whitespace-') as directory:
        root = pathlib.Path(directory)
        selection = root / 'selection'
        selection.mkdir()
        # Force the policy boundary even if the supplied file omits root=true.
        _ = (selection / '.editorconfig').write_text(
            'root = true\n' + re.sub(r'(?mi)^[ \t]*root\s*=.*$', '', pathlib.Path(policy).read_text()), encoding='utf-8'
        )
        work = root / 'work'
        work.mkdir()
        _ = (work / 'checker.json').write_text('{}', encoding='utf-8')
        for filename in files:
            source = pathlib.Path(filename)
            relative = source.absolute().relative_to(pathlib.Path.cwd())
            if '..' in relative.parts or source.is_symlink():
                raise ValueError(f'unsafe whitespace input: {filename}')
            properties = editorconfig.get_properties(str(selection / relative))
            _ = (work / '.editorconfig').write_text(
                'root = true\n[*]\n' + ''.join(f'{key} = {value}\n' for key, value in properties.items()),
                encoding='utf-8',
            )
            target = work / 'content'
            original = source.read_bytes()
            _ = target.write_bytes(original)
            result = subprocess.run(
                [
                    tool,
                    '--config',
                    str(work / 'checker.json'),
                    '--ignore-defaults',
                    '--no-color',
                    *(['--fix'] if mode == 'fix' else []),
                    *options,
                    str(target),
                ],
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            output = (result.stdout + result.stderr).replace(str(target), filename)
            output = output.replace(os.path.relpath(target), filename)
            if output:
                print(output, end='')
            if result.returncode:
                status = 1
            elif mode == 'fix' and target.read_bytes() != original:
                _ = source.write_bytes(target.read_bytes())
    return status


if __name__ == '__main__':
    sys.exit(main())
