"""Exercise runtime isolation and analysis-only PHPStan argument handling."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv.pop(1))

from phpstan import Settings, analysis_options, run


class AnalysisOptionsTest(unittest.TestCase):
    def test_analysis_options_and_separate_values(self) -> None:
        analysis_options(['--memory-limit', '1G', '--level=max', '-a', 'vendor/autoload.php', '-vvv', '--no-ansi'])

    def test_mutating_bypassing_and_scope_options_are_rejected(self) -> None:
        for option in (
            '--configuration=other.neon',
            '-cother.neon',
            '--generate-baseline',
            '--generate-baseline=baseline.neon',
            '--gener',
            '-bbaseline.neon',
            '--fix',
            '--fix-ignored',
            '--watch',
            '--pro',
            '--help',
            '-V',
            '--version',
            '--tmp-file=other.php',
            '--instead-of=src/app.php',
            '--',
            'src',
            'clear-result-cache',
        ):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, 'unsupported analysis option'):
                analysis_options([option])

    def test_missing_values_cannot_smuggle_another_flag(self) -> None:
        for options in (['--level'], ['--level='], ['--level', '--generate-baseline'], ['--level=--fix']):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'explicit option value'):
                analysis_options(options)


class RunnerTest(unittest.TestCase):
    def test_selected_runtime_and_configuration_with_failure_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            config = project / 'phpstan.neon'
            _ = config.write_text('parameters:\n    level: max\n')
            settings: Settings = {
                'root': 'project',
                'configFile': 'phpstan.neon',
                'php': sys.executable,
                'exe': 'tools/phpstan',
                'extraOptions': ['--memory-limit=1G'],
            }
            env = {'PATH': '/declared/tools', 'PHPRC': '/host/php.ini', 'PHP_INI_SCAN_DIR': '/host/extensions'}
            with (
                patch('phpstan.Path.cwd', return_value=root),
                patch.dict(os.environ, env, clear=True),
                patch('phpstan.subprocess.run', return_value=subprocess.CompletedProcess([], 23)) as execute,
            ):
                self.assertEqual(run(settings), 23)
                execute.assert_called_once_with(
                    ['tools/phpstan', 'analyse', '--configuration', str(config), '--no-progress', '--memory-limit=1G'],
                    cwd=project,
                    env={'PATH': f'{Path(sys.executable).parent}:/declared/tools', 'CI': '1'},
                    stdin=subprocess.DEVNULL,
                    check=False,
                )


if __name__ == '__main__':
    _ = unittest.main()
