"""PHPStan integration through the public configure API."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, cast, override

programs = cast(dict[str, str], json.loads(Path(sys.argv.pop(1)).read_text()))


class Invocation(TypedDict):
    cwd: str
    args: list[str]
    php: str
    env: dict[str, str]


class CoverageRow(TypedDict):
    path: str
    project_checks: list[dict[str, str]]


class CoverageReport(TypedDict):
    unmapped_project_checks: list[str]
    files: list[CoverageRow]


class PHPStanIntegration(unittest.TestCase):
    temp: tempfile.TemporaryDirectory[str]
    root: Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'work'

    @override
    def setUp(self) -> None:
        for source, destination in (
            (programs['source'], self.root),
            (programs['dependency'], self.root / 'alpha/vendor'),
        ):
            _ = shutil.copytree(source, destination)
            for path in [destination, *destination.rglob('*')]:
                path.chmod(0o755 if path.is_dir() or path.name == 'phpstan' else 0o644)
        (self.root / '.fixture-root').touch()

    def run_checker(
        self, name: str = 'lint', *, success: bool = True, cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> str:
        result = subprocess.run(
            [programs[name]],
            cwd=cwd or self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout)
        return result.stdout

    def break_collection_type(self) -> None:
        client = self.root / 'alpha/src/Client.php'
        _ = client.write_text(client.read_text().replace("['id']", "['name']"))

    def test_native_policy_dependencies_runtime_and_subdirectory_invocation(self) -> None:
        # No Composer installation or application environment is needed by the fixture.
        originals = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        _ = (self.root / 'alpha/phpstan.neon').write_text('invalid ambient configuration')
        output = self.run_checker(cwd=self.root / 'alpha/src')
        self.assertIn('0 files and 2 project checks, 0 failed', output)
        _ = self.run_checker('validate')
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_dependency_phpdoc_collection_and_project_continuation(self) -> None:
        self.break_collection_type()
        output = self.run_checker(success=False)
        self.assertIn('FAIL (phpstan-alpha): project', output)
        self.assertIn('should return int but returns string', output)
        self.assertNotIn('FAIL (phpstan-beta)', output)
        self.assertIn('2 project checks, 1 failed', output)
        _ = (self.root / 'beta/src/Math.php').write_text('<?php function bad(): int { return "bad"; }')
        output = self.run_checker(success=False)
        self.assertIn('FAIL (phpstan-beta)', output)
        self.assertIn('2 project checks, 2 failed', output)

    def test_response_shape_and_generic_file_exclusions(self) -> None:
        _ = (self.root / 'alpha/src/Shape.php').write_text(
            '<?php\n/** @return array{id: int, name: string} */\n'
            + 'function decodeResponse(): array { return ["id" => "bad", "name" => "router"]; }\n'
        )
        for program in ('lint', 'excluded', 'validate'):
            output = self.run_checker(program, success=False)
            self.assertIn('decodeResponse()', output)
            self.assertIn('should return array', output)

    def test_native_extension_loading(self) -> None:
        _ = (self.root / 'alpha/src/Environment.php').write_text(
            '<?php function rawEnvironment(): string|false { return getenv("FOO"); }\n'
        )
        self.assertIn('Fixture extension rejects getenv', self.run_checker(success=False))

    def test_invalid_paths_runtime_options_and_tool_failures(self) -> None:
        for name, message in (
            ('missingRoot', 'missing'),
            ('fileRoot', 'directory inside the repository'),
            ('escapingRoot', 'directory inside the repository'),
            ('missingConfig', 'missing.neon'),
            ('directoryConfig', 'file inside the project root'),
            ('escapingConfig', 'file inside the project root'),
            ('missingPhp', 'PHP interpreter is missing or not executable'),
            ('invalidPhp', 'PHP interpreter is missing or not executable'),
            ('failure', 'deliberate-phpstan-failure'),
            ('badOption', 'unsupported analysis option'),
        ):
            with self.subTest(name=name):
                output = self.run_checker(name, success=False)
                self.assertIn('FAIL (phpstan-alpha): project', output)
                self.assertIn(message, output)
        self.assertFalse((self.root / 'alpha/baseline.neon').exists())

    def test_symlink_escapes(self) -> None:
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'phpstan.neon').touch()
        shutil.rmtree(self.root / 'alpha/config')
        (self.root / 'alpha/config').symlink_to(outside, target_is_directory=True)
        self.assertIn('file inside the project root', self.run_checker(success=False))
        shutil.rmtree(self.root / 'beta')
        (self.root / 'beta').symlink_to(outside, target_is_directory=True)
        self.assertIn('directory inside the repository', self.run_checker(success=False))

    def test_malformed_native_configuration_and_missing_dependencies(self) -> None:
        config = self.root / 'alpha/config/phpstan.neon'
        original = config.read_text()
        _ = config.write_text('parameters:\n    invalidSetting: true\n')
        self.assertIn('Invalid configuration', self.run_checker(success=False))
        _ = config.write_text(original)
        shutil.rmtree(self.root / 'alpha/vendor')
        self.assertIn('autoload.php', self.run_checker(success=False))

    def test_package_executable_overrides_and_runtime_isolation(self) -> None:
        record = Path(self.temp.name) / 'record.jsonl'
        env = os.environ | {
            'PHPSTAN_RECORD': str(record),
            'PHPRC': '/unwanted/php.ini',
            'PHP_INI_SCAN_DIR': '/unwanted/extensions',
        }
        for program in ('recorded', 'exe'):
            _ = self.run_checker(program, env=env)
        rows = [cast(Invocation, json.loads(line)) for line in record.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['cwd'], str(self.root / 'alpha'))
            self.assertEqual(row['php'], programs['php'])
            self.assertEqual(
                row['args'][:4],
                ['analyse', '--configuration', str(self.root / 'alpha/config/phpstan.neon'), '--no-progress'],
            )
            for key in ('PHPRC', 'PHP_INI_SCAN_DIR'):
                self.assertNotIn(key, row['env'])
        self.assertEqual(rows[0]['args'][4:], ['--memory-limit=1G'])
        self.assertEqual(rows[1]['args'][4:], [])
        _ = self.run_checker('relativeExe', env=env)

    def test_coverage_declares_scope_without_running_phpstan(self) -> None:
        _ = subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        _ = subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        self.break_collection_type()
        for name in ('coverage', 'declaredCoverage'):
            output = subprocess.check_output([programs[name], '--json'], cwd=self.root, text=True)
            report = cast(CoverageReport, json.loads(output))
            expected = (
                ['lint:phpstan-beta'] if name == 'declaredCoverage' else ['lint:phpstan-alpha', 'lint:phpstan-beta']
            )
            self.assertEqual(report['unmapped_project_checks'], expected)
            row = next(row for row in report['files'] if row['path'] == 'alpha/src/Client.php')
            if name == 'declaredCoverage':
                self.assertEqual(row['project_checks'][0]['name'], 'lint:phpstan-alpha')
                self.assertEqual(row['project_checks'][0]['scope'], 'declared')


_ = unittest.main()
