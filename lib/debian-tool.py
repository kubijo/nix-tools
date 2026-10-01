"""Scoped adapter for the pinned debputy CLI; upstream has no file-list option."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(arguments):
    from debputy.commands.debputy_cmd.__main__ import main as debputy_main
    from debputy.linting.lint_util import LintReport
    from debputy.lsp.lsp_features import (
        CLI_DIAGNOSTIC_HANDLERS,
        CLI_FORMAT_FILE_HANDLERS,
        ensure_cli_lsp_features_are_loaded,
    )
    from pathspec import GitIgnoreSpec

    if not arguments or arguments[0] not in {'lint', 'reformat', 'coverage'}:
        raise ValueError('expected lint, reformat, or coverage')
    operation = arguments[0]
    if '--' in arguments:
        separator = arguments.index('--')
        files = arguments[separator + 1 :]
        arguments = arguments[:separator]
    else:
        files = None
    config = None
    if '--config' in arguments:
        index = arguments.index('--config')
        config = Path(arguments[index + 1]).resolve(strict=True)
        del arguments[index : index + 2]

    if operation in {'lint', 'coverage'} and any(
        arg.split('=', 1)[0] in {'--auto-fix', '--report-output', '--lint-report-format'} for arg in arguments
    ):
        raise ValueError('lint must remain read-only and use terminal diagnostics')

    ensure_cli_lsp_features_are_loaded()
    handlers = CLI_DIAGNOSTIC_HANDLERS if operation in {'lint', 'coverage'} else CLI_FORMAT_FILE_HANDLERS
    if operation in {'lint', 'coverage'}:
        if files is not None:
            raise ValueError('lint is project-scoped and accepts no file arguments')
        command = ['@fd@', '--hidden', '--no-require-git', '--show-errors', '--type', 'file', '--print0']
        exclusions = json.loads(os.environ.get('REPOCHK_EXCLUDES_JSON', '[]'))
        for pattern in exclusions:
            command.extend(['--exclude', pattern])
        command.extend(['.', '.'])
        result = subprocess.run(command, capture_output=True, check=False)
        # fd can return zero after skipping an unreadable directory.
        if result.stderr:
            raise ValueError(f'file discovery failed: {os.fsdecode(result.stderr)}')
        result.check_returncode()
        files = os.fsdecode(result.stdout).split('\0')
        selected = {Path(path).as_posix().removeprefix('./') for path in files if path}
        selected.intersection_update(handlers)
        if operation == 'coverage':
            print(json.dumps(sorted(selected)))
            return
        # A control-file check can report a missing/invalid related file. Honour
        # exclusions for those diagnostics as well as the primary handler selection.
        ignored = GitIgnoreSpec.from_lines(exclusions)
        report = LintReport.report_diagnostic

        def report_selected(self, diagnostic, **kwargs):
            data = diagnostic.data
            related = data.get('report_for_related_file') if isinstance(data, dict) else None
            if related and ignored.match_file(related):
                return
            report(self, diagnostic, **kwargs)

        LintReport.report_diagnostic = report_selected
    else:
        if files is None:
            raise ValueError('reformat requires -- followed by selected files')
        root = Path.cwd().resolve()
        selected = set()
        for filename in files:
            path = Path(filename)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'not a regular file: {filename}')
            relative = path.resolve().relative_to(root).as_posix()
            if relative not in handlers:
                raise ValueError(f'unsupported Debian formatting path: {filename}')
            selected.add(relative)
        # Upstream declines malformed deb822 without a failing exit status. Reject
        # it before any writes, including duplicate fields and malformed stanzas.
        from debian._deb822_repro import parse_deb822_file

        for filename in selected:
            with open(filename, encoding='utf-8') as source:
                parse_deb822_file(source)
            if os.path.lexists(filename + '.tmp'):
                raise ValueError(f'refusing to overwrite existing formatter temporary file: {filename}.tmp')
    if not selected:
        return
    for path in list(handlers):
        if path not in selected:
            del handlers[path]

    # Debputy deliberately reads debian/control as context even when its diagnostics
    # are excluded. Its configuration lookup uses XDG_CONFIG_DIR (singular).
    with tempfile.TemporaryDirectory(prefix='debputy-config-') as directory:
        os.environ['XDG_CONFIG_DIR'] = directory
        if config is not None:
            target = Path(directory) / 'debputy' / 'debputy-config.yaml'
            target.parent.mkdir()
            shutil.copyfile(config, target)
        sys.argv = ['debputy', *arguments]
        debputy_main()


def main():
    try:
        run(sys.argv[1:])
    except (ValueError, IndexError, OSError, subprocess.CalledProcessError) as error:
        print(f'debputy-nix-tools: {error}', file=sys.stderr)
        sys.exit(1)
