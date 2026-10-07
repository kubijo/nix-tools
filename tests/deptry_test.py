"""Deptry tests through the public configure API."""

import errno
import json
import os
import pty
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
    env: dict[str, str]


class CoverageRow(TypedDict):
    path: str
    project_checks: list[dict[str, str]]


class CoverageReport(TypedDict):
    unmapped_project_checks: list[str]
    files: list[CoverageRow]


class DeptryIntegration(unittest.TestCase):
    temp: tempfile.TemporaryDirectory[str]
    root: Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'work'

    @override
    def setUp(self) -> None:
        _ = shutil.copytree(programs['source'], self.root)
        _ = self.root.chmod(0o755)
        for path in self.root.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
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

    def edit_manifest(self, old: str, new: str) -> None:
        manifest = self.root / 'alpha/pyproject.toml'
        content = manifest.read_text()
        self.assertEqual(content.count(old), 1, f'manifest edit must match once: {old!r}')
        _ = manifest.write_text(content.replace(old, new))

    def test_independent_manifests_and_subdirectory_invocation(self) -> None:
        original = {str(path): path.read_bytes() for path in self.root.rglob('*.toml')}
        output = self.run_checker(cwd=self.root / 'alpha/src/acme/deep')
        self.assertIn('0 files and 2 project checks, 0 failed', output)
        _ = self.run_checker('validate')
        self.assertEqual(original, {str(path): path.read_bytes() for path in self.root.rglob('*.toml')})
        _ = (self.root / 'beta/src/app.py').write_text('import missing_beta\n')
        _ = (self.root / 'alpha/src/bad.py').write_text('import missing_alpha\n')
        output = self.run_checker(success=False)
        for expected in ('FAIL (deptry-alpha)', 'FAIL (deptry-beta)', '2 project checks, 2 failed'):
            self.assertIn(expected, output)
        # Overlapping roots must not duplicate diagnostics.
        self.assertEqual(output.count("'missing_alpha' imported"), 1, output)

    def test_namespace_discovery_and_existing_first_party(self) -> None:
        _ = self.run_checker()
        manifest = self.root / 'alpha/pyproject.toml'
        _ = (self.root / 'alpha/namespace-off.toml').write_text(
            manifest.read_text().replace(
                'experimental_namespace_package = true', 'experimental_namespace_package = false'
            )
        )
        output = self.run_checker('namespaceOff', success=False)
        self.assertIn("'acme' imported", output)
        self.assertNotIn("'provided_by_host' imported", output)
        self.assertNotIn("'internal_helper' imported", output)

    def test_nested_projects(self) -> None:
        nested = self.root / 'alpha/nested'
        nested.mkdir()
        _ = shutil.move(self.root / 'beta', nested / 'beta')
        _ = self.run_checker('nested', cwd=nested / 'beta/src')

    def test_transitive_rule_is_not_disabled(self) -> None:
        # Packaging is installed with deptry but undeclared here.
        _ = (self.root / 'alpha/src/transitive.py').write_text('import packaging\n')
        output = self.run_checker(success=False)
        self.assertIn('DEP003', output)
        self.assertIn("'packaging'", output)

    def test_generated_import_and_generic_excludes(self) -> None:
        _ = self.run_checker()
        self.edit_manifest('"fixture-protobuf", ', '')
        for program in ('lint', 'excluded', 'validate'):
            output = self.run_checker(program, success=False)
            self.assertIn('messages_pb2.py', output)
            self.assertIn('DEP001', output)

    def test_terminal_colors_and_no_color(self) -> None:
        _ = (self.root / 'alpha/src/bad.py').write_text('import missing_color_test\n')
        env = os.environ.copy()
        for key in ('NO_COLOR', 'FORCE_COLOR', 'CLICOLOR_FORCE'):
            _ = env.pop(key, None)
        self.assertNotIn('\x1b[', self.run_checker(success=False, env=env))
        for no_color in (False, True):
            with self.subTest(no_color=no_color):
                if no_color:
                    env |= {'NO_COLOR': '1', 'FORCE_COLOR': '1', 'CLICOLOR_FORCE': '1'}
                master, slave = pty.openpty()
                chunks: list[bytes] = []
                try:
                    with subprocess.Popen(
                        [programs['lint']],
                        cwd=self.root,
                        env=env,
                        stdout=slave,
                        stderr=slave,
                        stdin=subprocess.DEVNULL,
                    ) as process:
                        os.close(slave)
                        slave = -1
                        while True:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError as error:
                                if error.errno != errno.EIO:
                                    raise
                                break
                            if not chunk:
                                break
                            chunks.append(chunk)
                        self.assertEqual(process.wait(), 1)
                finally:
                    os.close(master)
                    if slave != -1:
                        os.close(slave)
                output = b''.join(chunks)
                self.assertIn(b'DEP001', output)
                self.assertEqual(b'\x1b[' in output, not no_color)

    def test_development_groups_remain_development_dependencies(self) -> None:
        _ = self.run_checker()
        for module in ('fixture_dev', 'fixture_optional_dev'):
            _ = (self.root / 'alpha/src/dev_import.py').write_text(f'import {module}\n')
            output = self.run_checker(success=False)
            self.assertIn('DEP004', output)
            self.assertIn(module, output)

    def test_cli_exception_is_narrow(self) -> None:
        _ = self.run_checker()
        self.edit_manifest(
            'dependencies = ["fixture-runtime", "fixture-protobuf", "fixture-cli"]',
            'dependencies = ["fixture-runtime", "fixture-protobuf", "fixture-cli", "fixture-unused"]',
        )
        output = self.run_checker(success=False)
        self.assertIn('DEP002', output)
        self.assertIn("'fixture-unused'", output)
        self.assertNotIn("'fixture-cli'", output)
        self.edit_manifest('DEP002 = ["fixture-cli"]', 'DEP002 = []')
        output = self.run_checker(success=False)
        self.assertIn("'fixture-cli'", output)

    def test_config_paths(self) -> None:
        config = self.root / 'alpha/config'
        config.mkdir()
        _ = shutil.move(self.root / 'alpha/pyproject.toml', config / 'pyproject.toml')
        _ = self.run_checker('relativeConfig')
        _ = self.run_checker('storeConfig')

    def test_invalid_paths_and_tool_failures(self) -> None:
        for name, message in (
            ('missingRoot', 'missing'),
            ('missingSource', 'missing'),
            ('fileSource', 'not a directory'),
            ('escapingSource', 'escapes'),
            ('missingConfig', 'missing.toml'),
            ('failure', 'deliberate-tool-failure'),
            ('badOption', 'No such option'),
            ('configOption', 'use configFile'),
        ):
            with self.subTest(name=name):
                output = self.run_checker(name, success=False)
                self.assertIn('FAIL (deptry-alpha): project', output)
                self.assertIn(message, output)
        _ = (self.root / 'alpha/pyproject.toml').write_text('[invalid toml')
        self.assertIn('deptry:', self.run_checker(success=False))

    def test_package_and_exe_overrides_once_with_directory_arguments(self) -> None:
        record = Path(self.temp.name) / 'record.jsonl'
        env = os.environ | {
            'DEPTRY_RECORD': str(record),
            'VIRTUAL_ENV': '/unwanted-venv',
            'PYTHONPATH': '/unwanted-modules',
            'PYTHONHOME': '/unwanted-python',
        }
        for program in ('recorded', 'exe'):
            _ = self.run_checker(program, env=env)
        rows = [cast(Invocation, json.loads(line)) for line in record.read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['cwd'], str(self.root / 'alpha'))
            args = row['args']
            self.assertEqual(args[args.index('--') + 1 :], ['.'])
            self.assertEqual(args[args.index('--config') + 1], str(self.root / 'alpha/pyproject.toml'))
            for module in ('acme', 'internal_helper', 'provided_by_host'):
                self.assertIn(module, args)
            for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
                self.assertNotIn(key, row['env'])
        self.assertIn('--verbose', rows[0]['args'])

    def test_coverage_project_metadata(self) -> None:
        _ = subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        _ = subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        # Coverage must not execute checks.
        _ = (self.root / 'alpha/src/app.py').write_text('import missing\n')
        for name in ('coverage', 'declaredCoverage'):
            output = subprocess.check_output([programs[name], '--json'], cwd=self.root, text=True)
            report = cast(CoverageReport, json.loads(output))
            expected = ['lint:deptry-beta'] if name == 'declaredCoverage' else ['lint:deptry-alpha', 'lint:deptry-beta']
            self.assertEqual(report['unmapped_project_checks'], expected)
            row = next(row for row in report['files'] if row['path'] == 'alpha/src/app.py')
            if name == 'declaredCoverage':
                self.assertEqual(row['project_checks'][0]['name'], 'lint:deptry-alpha')
                self.assertEqual(row['project_checks'][0]['scope'], 'declared')


_ = unittest.main()
