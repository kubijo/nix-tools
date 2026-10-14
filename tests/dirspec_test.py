"""Contracts for TOML directory assertions and the standalone CLI."""

import contextlib
import errno
import grp
import io
import os
import pwd
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import cast, override
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv.pop(1)).resolve()))
import dirspec as tool


class Terminal(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


class Fixture(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    base: Path
    root: Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'tree'
        self.root.mkdir()

    def write(self, name: str, data: str = 'data') -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(data)
        return path

    def spec(self, paths: dict[str, object], **options: object) -> tool.Spec:
        return tool.parse_spec({'version': 1, 'paths': paths, **options})

    def check(self, paths: dict[str, object], **options: object) -> tool.Report:
        return tool.check_tree(self.root, self.spec(paths, **options))

    def cli(self, content: str, *args: str) -> subprocess.CompletedProcess[str]:
        prescription = self.base / 'spec.toml'
        _ = prescription.write_text(content)
        return subprocess.run(
            [sys.executable, str(Path(tool.__file__)), 'tree', '--spec', 'spec.toml', *args],
            cwd=self.base,
            env={**os.environ, 'CI': 'true', 'NO_COLOR': '1', 'FORCE_COLOR': '0', 'CLICOLOR_FORCE': '0'},
            capture_output=True,
            text=True,
            check=False,
        )


class Structure(Fixture):
    def test_toml_rules_and_globs_include_hidden_paths(self) -> None:
        _ = self.write('dist/app.a.js')
        _ = self.write('dist/.cache/app.b.css')
        _ = self.write('icons/a1.svg')
        content = """
version = 1
strict = true
[paths]
"dist/**/app.?.{js,css}" = { type = "file", count = 2 }
"icons/[ab]?.svg" = { type = "file", count = 1 }
"""
        result = self.cli(content)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('0 failures, 0 undeclared', result.stdout)

    def test_braces_are_alternatives_and_star_does_not_cross_slashes(self) -> None:
        _ = self.write('locales/en.js')
        _ = self.write('nested/a/b.js')
        report = self.check({'locales/{en,cs}.js': {}, 'nested/*.js': {}})
        self.assertEqual(len(report.problems), 1)
        self.assertEqual(report.problems[0].pattern, 'nested/*.js')
        self.assertIn('got 0', report.problems[0].detail)

    def test_missing_requirements_fail_even_in_empty_trees(self) -> None:
        report = self.check({'README': {}, 'bin/app': {}})
        self.assertEqual(len(report.problems), 2)
        self.assertEqual(report.entries.keys(), {'.'})

    def test_optional_rules_still_validate_present_matches(self) -> None:
        self.assertFalse(self.check({'logs/*.log': {'min': 0, 'min_size': 1}}).problems)
        _ = self.write('logs/empty.log', '')
        report = self.check({'logs/*.log': {'min': 0, 'min_size': 1}})
        self.assertEqual(report.problems[0].path, 'logs/empty.log')
        self.assertIn('size:', report.problems[0].detail)

    def test_forbidden_and_bounded_counts(self) -> None:
        _ = self.write('a.map')
        _ = self.write('b.map')
        self.assertFalse(self.check({'*.map': {'min': 1, 'max': 2}}).problems)
        for assertions in ({'count': 0}, {'count': 1}, {'max': 1}, {'min': 3}):
            with self.subTest(assertions=assertions):
                report = self.check({'*.map': assertions})
                self.assertIn('got 2', report.problems[0].detail)
                self.assertEqual(report.failed, {'a.map', 'b.map'})

    def test_counts_precede_type_assertions(self) -> None:
        (self.root / 'named.js').mkdir()
        report = self.check({'*.js': {'type': 'file', 'count': 1}})
        self.assertEqual(len(report.problems), 1)
        self.assertIn('type:', report.problems[0].detail)

    def test_overlapping_rules_all_apply_regardless_of_order(self) -> None:
        _ = self.write('app.js', '')
        rules: dict[str, object] = {'*.js': {'min_size': 1}, 'app.js': {'type': 'directory'}}
        for paths in (rules, dict(reversed(list(rules.items())))):
            self.assertEqual(len(self.check(paths).problems), 2)

    def test_extras_default_to_allow_and_strict_keeps_structural_parents(self) -> None:
        _ = self.write('dist/assets/app.js')
        _ = self.write('extra')
        (self.root / 'empty').mkdir()
        paths: dict[str, object] = {'dist/assets/app.js': {}}
        loose = self.check(paths)
        self.assertFalse(loose.problems)
        self.assertEqual(loose.extras, {'extra', 'empty'})
        strict = self.check(paths, strict=True)
        self.assertEqual({problem.path for problem in strict.problems}, {'extra', 'empty'})

    def test_declaring_directory_does_not_declare_its_children(self) -> None:
        _ = self.write('assets/icon.svg')
        report = self.check({'assets': {'type': 'directory'}}, strict=True)
        self.assertEqual(report.extras, {'assets/icon.svg'})
        self.assertEqual(len(report.problems), 1)

    def test_exclusions_prune_subtrees_and_cannot_satisfy_rules(self) -> None:
        _ = self.write('.git/objects/hidden')
        _ = self.write('cache/private/hidden')
        _ = self.write('keep')
        report = self.check({'keep': {}}, strict=True, exclude=['.git', 'cache'])
        self.assertEqual(report.entries.keys(), {'.', 'keep'})
        self.assertFalse(report.problems)
        missing = self.check({'.git/objects/hidden': {}}, exclude=['.git'])
        self.assertIn('got 0', missing.problems[0].detail)

    def test_root_is_only_matched_explicitly(self) -> None:
        report = self.check({'.': {'type': 'directory', 'count': 1}, '**': {'count': 0}}, strict=True)
        self.assertFalse(report.problems)
        self.assertEqual(report.declared, {'.'})


class Traversal(Fixture):
    def directory_replacement(self, *, before_open: bool, symlink: bool) -> None:
        directory = self.root / 'directory'
        directory.mkdir()
        directory.chmod(0o700)
        original = directory.stat()
        outside = self.base / 'outside'
        outside.mkdir()
        _ = (outside / 'file').write_text('outside')
        outside_info = outside.stat()
        prescription = self.base / 'spec.toml'
        _ = prescription.write_text(
            'version = 1\nstrict = true\n[paths]\n'
            + '"directory" = { type = "directory", mode = 0o700 }\n"directory/file" = { type = "file" }\n'
        )
        replaced = False
        scanned_outside = False
        opened: list[int] = []
        real_scandir, real_open = os.scandir, os.open

        def replace() -> None:
            nonlocal replaced
            _ = directory.rename(self.base / 'retired')
            if symlink:
                directory.symlink_to(outside, target_is_directory=True)
            else:
                directory.mkdir()
                directory.chmod(0o755)
                _ = (directory / 'file').write_text('replacement')
            replaced = True

        def scan(path: int | Path) -> Iterator[os.DirEntry[str]]:
            nonlocal scanned_outside
            info = os.fstat(path) if isinstance(path, int) else path.stat()
            if not before_open and not replaced and os.path.samestat(info, original):
                replace()
            actual = os.fstat(path) if isinstance(path, int) else path.stat()
            scanned_outside |= os.path.samestat(actual, outside_info)
            return real_scandir(path)

        def open_directory(path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            if before_open and not replaced and path == 'directory' and dir_fd is not None:
                replace()
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            opened.append(descriptor)
            return descriptor

        with (
            patch.object(os, 'scandir', side_effect=scan),
            patch.object(os, 'open', side_effect=open_directory),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            result = tool.main([str(self.root), '--spec', str(prescription)])
        self.assertTrue(replaced, 'the replacement must happen during traversal')
        self.assertEqual(result, 2, output.getvalue() + error.getvalue())
        self.assertFalse(scanned_outside, 'a replacement symlink must never be traversed')
        self.assertNotIn('PASS', output.getvalue())
        self.assertIn('directory', error.getvalue())
        for descriptor in opened:
            with self.assertRaises(OSError) as closed:
                _ = os.fstat(descriptor)
            self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_directory_replaced_by_symlink_before_open_fails(self) -> None:
        self.directory_replacement(before_open=True, symlink=True)

    def test_directory_replaced_by_symlink_before_scandir_fails(self) -> None:
        self.directory_replacement(before_open=False, symlink=True)

    def test_directory_replaced_by_another_directory_before_open_fails(self) -> None:
        self.directory_replacement(before_open=True, symlink=False)

    def test_directory_replaced_by_another_directory_before_scandir_fails(self) -> None:
        self.directory_replacement(before_open=False, symlink=False)

    def test_root_symlink_is_still_supported(self) -> None:
        _ = self.write('file')
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        report = tool.check_tree(alias, self.spec({'file': {'type': 'file'}}, strict=True))
        self.assertFalse(report.problems)
        self.assertEqual(report.entries.keys(), {'.', 'file'})

    def test_wide_tree_does_not_exhaust_directory_descriptors(self) -> None:
        for index in range(64):
            _ = self.write(f'directory-{index:02}/file')
        active: set[int] = set()
        real_open, real_close = os.open, os.close

        def open_directory(path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            # Model a small descriptor budget without changing the process limit.
            if len(active) >= 4:
                raise OSError(errno.EMFILE, 'directory descriptor budget exhausted')
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            active.add(descriptor)
            return descriptor

        def close_directory(descriptor: int) -> None:
            real_close(descriptor)
            active.remove(descriptor)

        with (
            patch.object(os, 'open', side_effect=open_directory),
            patch.object(os, 'close', side_effect=close_directory),
        ):
            report = self.check({'*/file': {'count': 64}}, strict=True)
        self.assertFalse(report.problems)
        self.assertFalse(active, 'all directory descriptors must close after a successful scan')


class Metadata(Fixture):
    def test_exact_required_forbidden_and_special_mode_bits(self) -> None:
        path = self.write('app')
        for mode in (0o640, 0o755, 0o1755):
            with self.subTest(mode=mode):
                path.chmod(mode)
                self.assertFalse(self.check({'app': {'mode': mode}}).problems)
        path.chmod(0o755)
        self.assertFalse(self.check({'app': {'mode_required': 0o100, 'mode_forbidden': 0o022}}).problems)
        path.chmod(0o664)
        report = self.check({'app': {'mode': 0o755, 'mode_required': 0o100, 'mode_forbidden': 0o002}})
        self.assertEqual(len(report.problems), 2)
        self.assertIn('0o0664', report.problems[0].detail)
        self.assertTrue(self.check({'app': {'mode_forbidden': 0o020}}).problems)

    def test_directory_modes_and_size_restriction(self) -> None:
        directory = self.root / 'private'
        directory.mkdir(mode=0o700)
        self.assertFalse(self.check({'private': {'type': 'directory', 'mode': 0o700}}).problems)
        report = self.check({'private': {'size': 0}})
        self.assertIn('expected a regular file', report.problems[0].detail)

    def test_current_ownership_by_ids_and_names_and_mismatches(self) -> None:
        _ = self.write('app')
        uid, gid = os.getuid(), os.getgid()
        self.assertFalse(self.check({'app': {'owner': uid, 'group': gid}}).problems)
        self.assertFalse(
            self.check({'app': {'owner': pwd.getpwuid(uid).pw_name, 'group': grp.getgrgid(gid).gr_name}}).problems
        )
        report = self.check({'app': {'owner': uid + 1, 'group': gid + 1}})
        self.assertEqual(len(report.problems), 2)

    def test_exact_and_bounded_file_sizes(self) -> None:
        _ = self.write('app', 'abc')
        for attributes in ({'size': 3}, {'min_size': 3}, {'max_size': 3}, {'min_size': 1, 'max_size': 4}):
            self.assertFalse(self.check({'app': attributes}).problems)
        for attributes in ({'size': 4}, {'min_size': 4}, {'max_size': 2}):
            self.assertIn('got 3', self.check({'app': attributes}).problems[0].detail)

    def test_symlinks_are_leaves_including_loops_and_external_targets(self) -> None:
        _ = self.write('target', 'abc')
        (self.root / 'loop').symlink_to('.')
        (self.root / 'broken').symlink_to('absent')
        (self.root / 'external').symlink_to(self.base)
        report = self.check({'*': {'count': 4}}, strict=True)
        self.assertEqual(len(report.entries), 5)
        self.assertFalse(report.problems)
        self.assertEqual(report.entries['loop'].kind, 'symlink')

    def test_symlink_targets_and_resolution(self) -> None:
        target = self.write('target')
        link = self.root / 'link'
        link.symlink_to('target')
        attributes: dict[str, object] = {'type': 'symlink', 'link_target': 'target', 'link_exists': True}
        self.assertFalse(self.check({'link': attributes}).problems)
        self.assertTrue(self.check({'link': {'link_target': './target'}}).problems)
        target.unlink()
        self.assertTrue(self.check({'link': attributes}).problems)
        self.assertFalse(self.check({'link': {'link_exists': False}}).problems)
        link.unlink()
        link.symlink_to('link')
        self.assertFalse(self.check({'link': {'link_exists': False}}).problems)

    def test_symlink_permission_and_size_assertions_fail(self) -> None:
        (self.root / 'link').symlink_to('absent')
        report = self.check({'link': {'mode': 0o777, 'size': 0, 'owner': os.getuid()}})
        self.assertEqual(len(report.problems), 2)
        self.assertIn('unsupported for symlinks', report.problems[0].detail)
        _ = self.write('file')
        self.assertIn('expected a symlink', self.check({'file': {'link_exists': False}}).problems[0].detail)

    def test_symlink_resolution_is_cached_across_overlapping_rules(self) -> None:
        _ = self.write('target')
        link = self.root / 'link'
        link.symlink_to('target')
        with patch.object(tool, 'target_exists', wraps=tool.target_exists) as resolves:
            report = self.check({'link': {'link_exists': True}, 'l*': {'link_exists': True}})
        self.assertFalse(report.problems)
        resolves.assert_called_once_with(link)


class Configuration(Fixture):
    def test_invalid_top_level_fields(self) -> None:
        documents: list[dict[str, object]] = [
            {},
            {'version': True, 'paths': {'*': {}}},
            {'version': 2, 'paths': {'*': {}}},
            {'version': 1, 'paths': {}},
            {'version': 1, 'paths': []},
            {'version': 1, 'paths': {'*': {}}, 'strict': 1},
            {'version': 1, 'paths': {'*': {}}, 'exclude': 'cache'},
            {'version': 1, 'paths': {'*': {}}, 'exclude': [False]},
            {'version': 1, 'paths': {'*': {}}, 'unknown': True},
        ]
        for document in documents:
            with self.subTest(document=document), self.assertRaises(tool.SpecError):
                _ = tool.parse_spec(document)

    def test_invalid_rules_and_contradictions(self) -> None:
        rules: list[object] = [
            0,
            {'typo': 1},
            {'type': 'socket'},
            {'type': []},
            {'count': True},
            {'count': -1},
            {'count': '1'},
            {'count': 1, 'min': 0},
            {'count': 1, 'max': 1},
            {'min': 3, 'max': 2},
            {'max': 0},
            {'mode': '0755'},
            {'mode': 0o10000},
            {'mode_required': 0o10000},
            {'mode_required': 1, 'mode_forbidden': 1},
            {'mode': 0o644, 'mode_required': 0o100},
            {'mode': 0o644, 'mode_forbidden': 0o004},
            {'owner': True},
            {'owner': -1},
            {'group': []},
            {'size': 1, 'min_size': 0},
            {'min_size': 2, 'max_size': 1},
            {'size': -1},
            {'link_target': 4},
            {'link_target': ''},
            {'link_exists': 1},
        ]
        for rule in rules:
            with self.subTest(rule=rule), self.assertRaises(tool.SpecError):
                _ = self.spec({'*': rule})

    def test_invalid_paths_and_exclusions(self) -> None:
        for pattern in ('', '/tmp/file', '../file', 'a/../file', 'a/\x00'):
            with self.subTest(pattern=pattern), self.assertRaises(tool.SpecError):
                _ = self.spec({pattern: {}})
            with self.subTest(exclude=pattern), self.assertRaises(tool.SpecError):
                _ = self.spec({'*': {}}, exclude=[pattern])
        with self.assertRaisesRegex(tool.SpecError, 'root cannot be excluded'):
            _ = self.spec({'*': {}}, exclude=['.'])

    def test_unknown_accounts_are_configuration_errors(self) -> None:
        with patch.object(pwd, 'getpwnam', side_effect=KeyError), self.assertRaisesRegex(tool.SpecError, 'owner'):
            _ = self.spec({'*': {'owner': 'missing'}})
        with patch.object(grp, 'getgrnam', side_effect=KeyError), self.assertRaisesRegex(tool.SpecError, 'group'):
            _ = self.spec({'*': {'group': 'missing'}})

    def test_huge_brace_expansions_are_configuration_errors(self) -> None:
        with self.assertRaises(tool.SpecError):
            _ = self.spec({'file{1..2000}': {}})


class CommandLine(Fixture):
    def test_help_and_argument_errors(self) -> None:
        script = str(Path(tool.__file__))
        help_result = subprocess.run([sys.executable, script, '--help'], capture_output=True, text=True, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for option in ('dirspec', 'TARGET', '--spec FILE', '--strict', '--no-strict'):
            self.assertIn(option, help_result.stdout)
        for arguments in ([], ['tree'], ['tree', '--spec'], ['tree', '--spec', 'spec.toml', '--unknown']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, script, *arguments], capture_output=True, text=True, check=False
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_exit_codes_plain_output_and_strict_overrides(self) -> None:
        _ = self.write('app')
        content = 'version = 1\n[paths]\n"app" = {}\n'
        result = self.cli(content)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('\x1b', result.stdout)
        _ = self.write('extra')
        loose = self.cli(content)
        self.assertEqual(loose.returncode, 0)
        self.assertIn('+ extra', loose.stdout)
        strict = self.cli(content, '--strict')
        self.assertEqual(strict.returncode, 1)
        self.assertIn('undeclared entry', strict.stdout)
        override = self.cli(content.replace('version = 1', 'version = 1\nstrict = true'), '--no-strict')
        self.assertEqual(override.returncode, 0)
        self.assertEqual(self.cli('version = 1\n[paths]\n"missing" = {}').returncode, 1)
        invalid = self.cli('version = 2\n[paths]\n"app" = {}')
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, '')
        self.assertIn('version: expected 1', invalid.stderr)

    def test_malformed_toml_missing_specs_and_invalid_targets(self) -> None:
        for content in ('version = [', 'version = 1\n[paths]\n"app" = {}\n"app" = {}'):
            result = self.cli(content)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('Traceback', result.stderr)
        _ = self.write('app')
        with contextlib.redirect_stderr(io.StringIO()) as error:
            code = tool.main([str(self.root), '--spec', str(self.base / 'absent')])
        self.assertEqual(code, 2)
        self.assertTrue(error.getvalue())
        specification = self.base / 'valid.toml'
        _ = specification.write_text('version = 1\n[paths]\n"." = {}')
        for target in (self.root / 'app', self.root / 'missing'):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tool.main([str(target), '--spec', str(specification)]), 2)

    def test_io_errors_fail_instead_of_skipping_entries(self) -> None:
        specification = self.base / 'valid.toml'
        _ = specification.write_text('version = 1\n[paths]\n"." = {}')
        with (
            patch.object(os, 'scandir', side_effect=PermissionError(errno.EACCES, 'denied')),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(tool.main([str(self.root), '--spec', str(specification)]), 2)
        self.assertIn('denied', output.getvalue())
        with (
            patch.object(Path, 'stat', side_effect=PermissionError(errno.EACCES, 'denied')),
            self.assertRaises(PermissionError),
        ):
            _ = tool.target_exists(self.root)

    def test_tree_order_escaping_and_expected_actual_diagnostics(self) -> None:
        _ = self.write('z')
        _ = self.write('a/[red].txt', '')
        _ = self.write('line\nbreak')
        spec = self.spec({'a/[[]red].txt': {'min_size': 1}, 'z': {}, 'line\nbreak': {}})
        report = tool.check_tree(self.root, spec)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            tool.render(report)
        text = output.getvalue()
        self.assertLess(text.index('a/'), text.index(' z'))
        self.assertIn('[red].txt', text)
        self.assertIn('line\\nbreak', text)
        self.assertIn('expected at least 1 bytes, got 0', text)
        self.assertEqual(
            self.cli('version = 1\n[paths]\n"**" = {}').stdout, self.cli('version = 1\n[paths]\n"**" = {}').stdout
        )

    def test_terminal_color_and_no_color(self) -> None:
        report = self.check({'.': {}})
        for variables, expect_color in (
            ({'FORCE_COLOR': '1'}, True),
            ({'FORCE_COLOR': '1', 'NO_COLOR': '1', 'CI': 'true'}, True),
            ({'NO_COLOR': '1'}, False),
            ({'CI': 'true'}, False),
        ):
            with self.subTest(variables=variables), patch.dict(os.environ, variables, clear=True):
                with contextlib.redirect_stdout(Terminal()) as output:
                    tool.render(report)
                self.assertEqual('\x1b[' in output.getvalue(), expect_color)
                self.assertEqual('\x1b[32mPASS' in output.getvalue(), expect_color)

    def test_deep_tree_output_keeps_every_entry(self) -> None:
        name = '/'.join([f'level-{index:02}' for index in range(40)] + ['leaf.txt'])
        _ = self.write(name)
        report = self.check({'**': {}})
        for width in (20, 80):
            for terminal in (False, True):
                with (
                    self.subTest(width=width, terminal=terminal),
                    patch.dict(os.environ, {'COLUMNS': str(width)}, clear=True),
                ):
                    with contextlib.redirect_stdout(Terminal() if terminal else io.StringIO()) as output:
                        tool.render(report)
                    text = output.getvalue()
                    self.assertEqual(text.count('✓'), len(report.entries))
                    self.assertIn(name, text)
                    self.assertIn('PASS', text)

    def test_deep_tree_output_keeps_failed_and_undeclared_paths(self) -> None:
        name = '/'.join([f'level-{index:02}' for index in range(40)] + ['leaf.txt'])
        _ = self.write(name, '')
        extra = str(Path(name).with_name('extra.txt'))
        _ = self.write(extra)
        report = self.check({name: {'min_size': 1}})
        with (
            patch.dict(os.environ, {'COLUMNS': '80', 'NO_COLOR': '1'}, clear=True),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            tool.render(report)
        text = output.getvalue()
        self.assertIn(f'✗ {name}', text)
        self.assertIn(f'+ {extra}', text)
        self.assertEqual(text.count('✗') + text.count('+'), len(report.entries))
        self.assertIn('FAIL', text)

    def test_documented_example_parses(self) -> None:
        example = Path(__file__).parent.parent / 'examples' / 'dirspec' / 'prescription.toml'
        document = cast(dict[str, object], cast(object, tomllib.loads(example.read_text())))
        self.assertTrue(tool.parse_spec(document).rules)

    def test_documented_scenarios_produce_expected_results(self) -> None:
        script = Path(__file__).parent.parent / 'examples' / 'dirspec' / 'run.py'
        result = subprocess.run(
            [sys.executable, str(script)],
            env={
                **os.environ,
                'NO_COLOR': '1',
                'FORCE_COLOR': '0',
                'CLICOLOR_FORCE': '0',
                'CI': 'true',
                'COLUMNS': '200',
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.count('Checker exit:'), 9)
        self.assertEqual(result.stdout.count('Checker exit: 0 (expected 0).'), 2)
        self.assertEqual(result.stdout.count('Checker exit: 1 (expected 1).'), 7)
        for diagnostic in ('mode_required:', 'link_exists:', 'count:', 'size:', 'undeclared entry'):
            self.assertIn(diagnostic, result.stdout)
        documented = [
            match.group(1)
            for match in re.finditer(r'```text\n(.*?)\n```', (script.parent / 'README.md').read_text(), flags=re.DOTALL)
        ]
        self.assertTrue(documented)
        normalized = re.sub(r'(?m)^([✓✗]) /[^\n]*/tree/$', r'\1 …/tree/', result.stdout)
        for expected in documented:
            self.assertIn(expected, normalized)


if __name__ == '__main__':
    _ = unittest.main()
