"""Coverage CLI flags and the bare-Python recorder contract."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv.pop(1)).resolve()))
import coverage as tool


class CoverageCLI(unittest.TestCase):
    def test_help_and_invalid_arguments(self) -> None:
        script = str(Path(tool.__file__))
        help_result = subprocess.run([sys.executable, script, '--help'], capture_output=True, text=True, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for option in ('--json', '--check', 'Audit configuration'):
            self.assertIn(option, help_result.stdout)
        for arguments in ([], ['--unknown'], ['--json'], ['--record']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, script, *arguments], capture_output=True, text=True, check=False
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_report_flags_and_repeated_invocations(self) -> None:
        audit: tool.Audit = {
            'version': 1,
            'scope': 'tracked source',
            'files': [],
            'untracked': [],
            'ignored': [],
            'unmapped_project_checks': [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            _ = config.write_text('{}')
            with patch.object(tool, 'report', return_value=audit):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(tool.main([str(config), '--json', '--check']), 0)
                self.assertEqual(json.loads(output.getvalue()), audit)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(tool.main([str(config)]), 0)
                self.assertEqual(output.getvalue(), 'tracked source\n')
                audit['untracked'] = [{'path': 'extra.py', 'reason': 'untracked'}]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(tool.main([str(config), '--check']), 1)
                    self.assertEqual(tool.main([str(config), '--json']), 0)

    def test_recorder_needs_no_site_packages_and_preserves_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = ['formatter', '--flag', 'file with spaces', '-odd.py']
            result = subprocess.run(
                [sys.executable, '-S', str(Path(tool.__file__)), '--record', *arguments],
                env={**os.environ, 'NIX_TOOLS_COVERAGE_RECORDS': temporary},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            records = list(Path(temporary).glob('*.json'))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text()), [arguments[0], arguments[1:]])
            self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    _ = unittest.main()
