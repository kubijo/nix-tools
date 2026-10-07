"""Hermetic contracts for dependency inventories, failure reporting and source preservation."""

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterator, Sequence
from email.message import Message
from http.client import HTTPMessage
from typing import Self, cast, override
from unittest.mock import MagicMock, Mock, patch

from rich.style import Style

sys.path.insert(0, sys.argv.pop(1))
import common
import main
import network
import releases
import sources
from providers import PROVIDERS, cargo, composer, npm, pnpm, uv, yarn
from providers.contract import ProviderConfig
from terminal_env import AGENT_ENVS


class Terminal(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


class Fixture(unittest.TestCase):
    temporary: tempfile.TemporaryDirectory[str]
    root: pathlib.Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def write(self, name: str, text: str) -> pathlib.Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text)
        return path

    def js(self, name: str, data: object) -> pathlib.Path:
        return self.write(name, json.dumps(data))

    def tool(self, body: str) -> str:
        path = self.write('tool', f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
        return str(path)


class Projects(Fixture):
    def inventory(self, providers: dict[str, main.ProviderSettings], concurrency: int = 2) -> main.Inventory:
        return {'providers': providers, 'tools': [], 'releases': [], 'adapters': [], 'concurrency': concurrency}

    def test_legacy_roots_and_all_package_providers(self) -> None:
        folder = self.root / 'nested'
        folder.mkdir()
        configurations: tuple[main.ProviderSettings, ...] = ({'exe': 'tool'}, {'exe': 'tool', 'root': 'nested'})
        for provider in PROVIDERS:
            for settings in configurations:
                with self.subTest(provider=provider, settings=settings):
                    config = self.inventory({provider: settings})
                    mock = Mock(return_value=[common.Result(provider, 'same', 'lock', 'up-to-date')])
                    with patch.dict(PROVIDERS, {provider: mock}):
                        rows = main.collect_jobs(config, self.root, network.Client(timeout=7))
                    mock.assert_called_once_with(settings, folder if 'root' in settings else self.root, 7)
                    self.assertEqual(
                        [(row.provider, row.project, row.name) for row in rows], [(provider, 'default', 'same')]
                    )

    def test_named_projects_share_provider_options_and_retain_partial_results(self) -> None:
        for name in ('good', 'partial', 'failed'):
            (self.root / name).mkdir()

        def report(options: ProviderConfig, root: pathlib.Path, timeout: float) -> Iterator[common.Result]:
            self.assertEqual(options, {'exe': 'pinned'})
            self.assertEqual(timeout, 7)
            if root.name == 'failed':
                raise RuntimeError('private-secret')
            yield common.Result('uv', 'same', 'uv.lock', 'outdated', '1', latest='2')
            if root.name == 'partial':
                raise common.Failure('Missing dependency inventory')

        config = self.inventory(
            {
                'uv': {
                    'exe': 'pinned',
                    'projects': {name: {'root': name} for name in ('partial', 'missing', 'good', 'failed')},
                }
            }
        )
        with patch.dict(PROVIDERS, {'uv': report}):
            rows = main.collect_jobs(config, self.root, network.Client(timeout=7))
        self.assertEqual(
            [(r.project, r.state) for r in rows],
            [
                ('partial', 'outdated'),
                ('partial', 'error'),
                ('missing', 'error'),
                ('good', 'outdated'),
                ('failed', 'error'),
            ],
        )
        self.assertEqual(common.summary(rows), ('ERROR', 2))
        self.assertNotIn('private-secret', str([r.json() for r in rows]))

    def test_root_validation_and_equivalent_roots_reject_all_aliases(self) -> None:
        (self.root / 'real').mkdir()
        (self.root / 'alias').symlink_to(self.root / 'real', target_is_directory=True)
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        (self.root / 'loop').symlink_to('loop', target_is_directory=True)
        _ = self.write('file', 'not a directory')
        paths = {
            'one': './real',
            'two': 'real/.',
            'three': 'alias',
            'escape': 'escape',
            'parent': '../',
            'absolute': str(self.root),
            'empty': '',
            'file': 'file',
            'loop': 'loop',
            'nul': 'bad\0name',
            'good': '.',
        }
        config = self.inventory({'uv': {'projects': {name: {'root': path} for name, path in paths.items()}}})
        mock = Mock(return_value=[common.Result('uv', 'same', 'uv.lock', 'up-to-date')])
        with patch.dict(PROVIDERS, {'uv': mock}):
            rows = main.collect_jobs(config, self.root, network.Client())
        self.assertEqual(mock.call_count, 1)
        self.assertEqual({r.project for r in rows if r.state == 'error'}, set(paths) - {'good'})
        for row in rows[:3]:
            self.assertIn('Duplicate project root: one, three, two', row.detail)
        self.assertEqual(rows[-1].project, 'good')

    def test_duplicate_filesystem_identity_without_matching_path_spelling(self) -> None:
        for name in ('real', 'alias', 'good'):
            (self.root / name).mkdir()
        original_stat = pathlib.Path.stat

        def stat(path: pathlib.Path, *, follow_symlinks: bool = True) -> os.stat_result:
            target = self.root / 'real' if path == self.root / 'alias' else path
            return original_stat(target, follow_symlinks=follow_symlinks)

        config = self.inventory({'uv': {'projects': {name: {'root': name} for name in ('real', 'alias', 'good')}}})
        report = Mock(return_value=[common.Result('uv', 'same', 'uv.lock', 'up-to-date')])
        with patch.object(pathlib.Path, 'stat', stat), patch.dict(PROVIDERS, {'uv': report}):
            rows = main.collect_jobs(config, self.root, network.Client())
        self.assertEqual(
            [(r.project, r.state) for r in rows], [('real', 'error'), ('alias', 'error'), ('good', 'up-to-date')]
        )
        report.assert_called_once()
        self.assertTrue(all('Duplicate project root' in row.detail for row in rows[:2]))

    def test_global_concurrency_and_deterministic_order(self) -> None:
        lock = threading.Lock()
        overlap = threading.Barrier(2)
        active = 0
        maximum = 0
        calls = 0

        def report(_options: ProviderConfig, root: pathlib.Path, _timeout: float) -> Iterator[common.Result]:
            nonlocal active, maximum, calls
            with lock:
                active += 1
                calls += 1
                maximum = max(maximum, active)
            try:
                _ = overlap.wait(2)
                yield common.Result(root.parent.name, 'same', 'lock', 'outdated', '1', latest='2')
            finally:
                with lock:
                    active -= 1

        providers: dict[str, main.ProviderSettings] = {}
        for provider in ('uv', 'pnpm'):
            projects: dict[str, main.ProjectConfig] = {}
            for name in ('alpha', 'beta'):
                path = f'{provider}/{name}'
                (self.root / path).mkdir(parents=True)
                projects[name] = {'root': path}
            providers[provider] = {'projects': projects}

        def release(_client: network.Client, item: sources.ReleaseEntry, *, root: pathlib.Path) -> common.Result:
            (result,) = report({}, root / 'tools' / item['name'], 7)
            return result

        config = self.inventory(providers)
        config['tools'] = [{'name': name} for name in ('first', 'second')]
        with (
            patch.dict(PROVIDERS, {'uv': report, 'pnpm': report}),
            patch.object(main, 'release_entry', side_effect=release),
        ):
            rows = main.collect_jobs(config, self.root, network.Client())
        self.assertEqual(maximum, 2)
        self.assertEqual(calls, 6)
        self.assertEqual(
            [(r.provider, r.project, r.state) for r in rows],
            [
                ('uv', 'alpha', 'outdated'),
                ('uv', 'beta', 'outdated'),
                ('pnpm', 'alpha', 'outdated'),
                ('pnpm', 'beta', 'outdated'),
                ('tools', '', 'outdated'),
                ('tools', '', 'outdated'),
            ],
        )

    def test_invalid_project_configuration_preserves_other_providers(self) -> None:
        invalid: list[main.ProviderSettings] = [
            {'projects': {}},
            {'root': '.', 'projects': {'app': {'root': '.'}}},
            {'projects': {'bad/name': {'root': '.'}}},
        ]
        for settings in invalid:
            with self.subTest(settings=settings):
                config = self.inventory({'uv': settings, 'pnpm': {}})
                uv_report = Mock(side_effect=AssertionError('Invalid configuration must not execute'))
                pnpm_report = Mock(return_value=[common.Result('pnpm', 'same', 'lock', 'up-to-date')])
                with patch.dict(PROVIDERS, {'uv': uv_report, 'pnpm': pnpm_report}):
                    rows = main.collect_jobs(config, self.root, network.Client())
                uv_report.assert_not_called()
                self.assertEqual([(r.provider, r.state) for r in rows], [('uv', 'error'), ('pnpm', 'up-to-date')])
                self.assertEqual(rows[-1].project, 'default')
                self.assertEqual(common.summary(rows), ('ERROR', 2))

    def test_malformed_runtime_projects_retain_successful_results(self) -> None:
        malformed: tuple[object, ...] = ({}, None, {'root': None}, {'root': 1})
        pnpm_report = Mock(return_value=[common.Result('pnpm', 'same', 'lock', 'up-to-date')])
        for project in malformed:
            with self.subTest(project=project):
                raw: object = {'projects': {'bad': project, 'good': {'root': '.'}}}
                config = self.inventory({'uv': cast(main.ProviderSettings, cast(object, raw)), 'pnpm': {}})
                uv_report = Mock(return_value=[common.Result('uv', 'same', 'uv.lock', 'up-to-date')])
                with patch.dict(PROVIDERS, {'uv': uv_report, 'pnpm': pnpm_report}):
                    rows = main.collect_jobs(config, self.root, network.Client())
                self.assertEqual(
                    [(r.provider, r.project, r.state) for r in rows],
                    [('uv', 'bad', 'error'), ('uv', 'good', 'up-to-date'), ('pnpm', 'default', 'up-to-date')],
                )
                uv_report.assert_called_once()
                self.assertEqual(common.summary(rows), ('ERROR', 2))
        raw = {'projects': ['invalid']}
        config = self.inventory({'uv': cast(main.ProviderSettings, cast(object, raw)), 'pnpm': {}})
        with patch.dict(PROVIDERS, {'pnpm': pnpm_report}):
            rows = main.collect_jobs(config, self.root, network.Client())
        self.assertEqual([(r.provider, r.state) for r in rows], [('uv', 'error'), ('pnpm', 'up-to-date')])

    def test_report_attribution_and_schema_boundary(self) -> None:
        rows = [
            common.Result('uv', 'same', 'uv.lock', 'outdated', '1', latest='2', project=name)
            for name in ('tooling', 'api')
        ]
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.report(rows, False), 1)
        for name in ('tooling', 'api'):
            self.assertIn(f'uv/{name}: outdated same 1 → 2', output.getvalue())
        with (
            patch.dict(os.environ, {'TERM': 'xterm', 'COLUMNS': '120'}, clear=True),
            contextlib.redirect_stdout(Terminal()) as output,
        ):
            self.assertEqual(main.report(rows, False), 1)
        for name in ('tooling', 'api'):
            self.assertIn(f'uv/{name}', output.getvalue())
        self.assertEqual(output.getvalue().count('same'), 2)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.report(rows, True), 1)
        document = cast(dict[str, object], json.loads(output.getvalue()))
        self.assertEqual(document['schemaVersion'], 2)
        self.assertEqual(document['results'], [r.json() for r in rows])
        legacy_adapter = {'schemaVersion': 1, 'results': [{'name': 'same', 'state': 'up-to-date'}]}
        self.assertEqual(common.validate_adapter(legacy_adapter, 'adapter:test', '.')[0].project, '')
        with self.assertRaises(common.Failure):
            _ = common.validate_adapter({**legacy_adapter, 'schemaVersion': 2}, 'adapter:test', '.')


class Protocol(Fixture):
    def test_registered_providers_dispatch_with_public_names(self) -> None:
        names = {'cargo', 'composer', 'npm', 'pnpm', 'uv', 'yarn'}
        self.assertEqual(set(PROVIDERS), names)
        settings: dict[str, main.ProviderSettings] = {name: {'exe': name} for name in names}
        reports = {name: Mock(return_value=[common.Result(name, 'fixture', '.', 'up-to-date')]) for name in names}
        config: main.Inventory = {'providers': settings, 'tools': [], 'releases': [], 'adapters': []}
        client = network.Client(timeout=7)
        with patch.dict(PROVIDERS, reports):
            jobs = main.jobs(config, self.root, client)
            self.assertEqual({job[0] for job in jobs}, names)
            for job in jobs:
                rows = main.guarded(*job)
                self.assertEqual([(row.provider, row.state) for row in rows], [(job[0], 'up-to-date')])
                reports[job[0]].assert_called_once_with(settings[job[0]], self.root, 7)

    def test_runtime_version_discovery_reuses_release_lookup_and_preserves_source(self) -> None:
        original = self.write('source', 'unchanged')
        command = self.tool('import pathlib\npathlib.Path("source").write_text("mutated")\nprint("tool 1.2.3")\n')
        client = network.Client()
        client.release = Mock(return_value=('v2.0.0', '2.0.0'))
        item: sources.ReleaseEntry = {
            'name': 'host',
            'repo': 'example/tool',
            'versionCommand': [command],
            'versionPattern': r'tool (?P<version>\d+\.\d+\.\d+)',
        }
        row = sources.release_entry(client, item, 'releases', self.root)
        self.assertEqual((row.current, row.latest, row.state), ('1.2.3', '2.0.0', 'outdated'))
        client.release.assert_called_once_with('example/tool', tags=False)
        self.assertEqual(original.read_text(), 'unchanged')
        for output in ('', 'tool 1.2.3\nnoise', 'private-token'):
            with patch.object(sources, 'run', return_value=(output, '')), self.assertRaises(common.Failure):
                _ = sources.release_entry(client, item, root=self.root)
        for error in (common.Failure('Provider command timed out'), common.Failure('Provider command failed')):
            with patch.object(sources, 'run', side_effect=error), self.assertRaises(common.Failure):
                _ = sources.release_entry(client, item, root=self.root)

    def test_static_skips_do_not_copy_source_or_execute_commands(self) -> None:
        config: main.Inventory = {
            'providers': {},
            'tools': [],
            'releases': [],
            'adapters': [],
            'skips': [{'name': 'lint.local', 'source': 'lint.local', 'skip': 'Versioned here'}],
        }
        with patch.object(sources, 'snapshot', side_effect=AssertionError('No copy needed')):
            rows = [row for job in main.jobs(config, self.root, network.Client()) for row in main.guarded(*job)]
        self.assertEqual(
            [(row.name, row.state, row.detail) for row in rows], [('lint.local', 'skipped', 'Versioned here')]
        )
        self.assertEqual(common.summary(rows)[1], 0)

    def test_exit_precedence_and_empty_reports(self) -> None:
        for states, expected in [
            ([], 2),
            (['up-to-date'], 0),
            (['pinned', 'skipped'], 0),
            (['outdated'], 1),
            (['outdated', 'error'], 2),
            (['unknown'], 2),
            (['blocked'], 2),
        ]:
            with self.subTest(states=states):
                self.assertEqual(
                    common.summary([common.Result('test', 'name', '.', state) for state in states])[1], expected
                )

    def test_versions(self) -> None:
        for before, after, expected in [
            ('3.9', '3.14', 'outdated'),
            ('v1.2.0', '1.2', 'up-to-date'),
            ('php-8.5.1', '8.5.0', 'unknown'),
            ('0.5.8-unstable-2026-07-17', '0.5.8', 'ahead'),
            ('0.5.8-unstable-2026-07-17', '0.5.9', 'outdated'),
            ('snapshot', 'v1', 'unknown'),
        ]:
            self.assertEqual(common.compare('test', 'x', '.', before, after).state, expected)

    def test_adapter_protocol(self) -> None:
        good = {'schemaVersion': 1, 'results': [{'name': 'thing', 'state': 'outdated', 'current': '1', 'latest': '2'}]}
        self.assertEqual(common.validate_adapter(good, 'custom', '.')[0].latest, '2')
        bad_values: list[object] = [
            None,
            [],
            {},
            {'schemaVersion': 2, 'results': []},
            {'schemaVersion': 1, 'results': []},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'success'}]},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'up-to-date', 'secret': 'x'}]},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'up-to-date', 'current': 1}]},
        ]
        for bad in bad_values:
            with self.subTest(bad=bad), self.assertRaises(common.Failure):
                _ = common.validate_adapter(bad, 'custom', '.')

    def test_partial_results_and_sensitive_exceptions(self) -> None:
        def partial():
            yield common.Result('p', 'first', '.', 'outdated')
            raise ValueError('secret-token')

        rows = main.guarded('p', 'next', '.', partial)
        self.assertEqual([row.state for row in rows], ['outdated', 'error'])
        self.assertNotIn('secret-token', rows[-1].detail)
        self.assertEqual(main.guarded('p', 'x', '.', list)[0].state, 'error')
        self.assertEqual(main.guarded('p', 'x', '.', lambda: [1])[0].state, 'error')

    def test_redaction_and_json_output(self) -> None:
        with patch.dict(os.environ, {'GH_TOKEN': 'sensitive-token'}):
            value = main.clean('\x1b[31mhttps://user:pass@example.test/?token=abc sensitive-token')
        self.assertNotIn('user:pass', value)
        self.assertNotIn('sensitive-token', value)
        self.assertNotIn('abc', value)
        self.assertNotIn('\x1b', value)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main.report([common.Result('x', 'name', '.', 'outdated', '1', '1.1', '2')], True)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['schemaVersion'], 2)

    def test_unknown_is_gray_only_on_interactive_text_output(self) -> None:
        row = common.Result('uv', 'package', 'uv.lock', 'unknown', '1.0')
        for json_output, no_color, colored in ((False, False, True), (False, True, False), (True, False, False)):
            with self.subTest(json_output=json_output, no_color=no_color), patch.dict(os.environ, {}, clear=True):
                output = Terminal()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main.report([row], json_output, no_color), 2)
                self.assertEqual('\x1b[90m' in output.getvalue(), colored)
        with patch.dict(os.environ, {'NO_COLOR': '1'}):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                _ = main.report([row], False)
            self.assertNotIn('\x1b', output.getvalue())

    def test_force_color_overrides_no_color_and_nonterminal_heuristics(self) -> None:
        row = common.Result('uv', 'package', 'uv.lock', 'unknown', '1.0')
        for environment, no_color, styled in (
            ({'FORCE_COLOR': '1', 'NO_COLOR': '1', 'TERM': 'dumb', 'CI': '1'}, True, True),
            ({'FORCE_COLOR': '0', 'NO_COLOR': '1', 'TERM': 'xterm-256color'}, False, False),
            ({'FORCE_COLOR': '0', 'TERM': 'xterm-256color'}, False, False),
            ({'FORCE_COLOR': '1', 'TERM': 'dumb'}, False, True),
            ({'TERM': 'xterm-256color'}, False, False),
        ):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main.report([row], False, no_color), 2)
                self.assertEqual('\x1b[' in output.getvalue(), styled)

    def test_agent_markers_select_terse_output_even_on_a_tty(self) -> None:
        row = common.Result('tools', 'tool', '.', 'outdated', '1', latest='2')
        for marker in AGENT_ENVS:
            with self.subTest(marker=marker), patch.dict(os.environ, {marker: '1', 'TERM': 'xterm'}, clear=True):
                output = Terminal()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main.report([row], False), 1)
                    self.assertEqual(main.output_mode(False), (False, False, False))
                self.assertIn('tools: outdated tool 1 → 2', output.getvalue())
                self.assertNotIn('│', output.getvalue())
                self.assertNotIn('\x1b', output.getvalue())

        with (
            patch.dict(os.environ, {'CODEX_THREAD_ID': '', 'TERM': 'xterm'}, clear=True),
            contextlib.redirect_stdout(Terminal()),
        ):
            self.assertEqual(main.output_mode(False), (True, True, False))
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'thread', 'FORCE_COLOR': '1', 'TERM': 'xterm'}, clear=True):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertIn('│', output.getvalue())
            self.assertIn('\x1b[', output.getvalue())

    def test_snapshot_preserves_files_and_rejects_links(self) -> None:
        original = self.write('manifest', 'original')
        original.chmod(0o751)
        _ = self.write('.git/index', 'index')
        _ = self.write('.venv/large-file', 'environment')
        with common.snapshot(self.root) as work:
            self.assertFalse((work / '.git').exists())
            self.assertFalse((work / '.venv').exists())
            _ = (work / 'manifest').write_text('changed')
        self.assertEqual(original.read_text(), 'original')
        self.assertEqual(original.stat().st_mode & 0o777, 0o751)
        (self.root / 'outside').symlink_to('/etc')
        with self.assertRaises(common.Failure), common.snapshot(self.root):
            pass
        (self.root / 'outside').unlink()
        (self.root / 'loop').symlink_to(self.root)
        with self.assertRaises(common.Failure), common.snapshot(self.root):
            pass

    def test_command_errors_and_timeout(self) -> None:
        bad = self.tool('import sys\nprint("private-credential", file=sys.stderr)\nsys.exit(3)\n')
        with self.assertRaises(common.Failure) as failure:
            _ = common.run([bad], self.root, 2)
        self.assertNotIn('private-credential', str(failure.exception))
        sleepy = self.tool('import time\ntime.sleep(10)\n')
        with self.assertRaisesRegex(common.Failure, 'timed out'):
            _ = common.run([sleepy], self.root, 0.05)
        bad = self.tool('print("not json")\n')
        with self.assertRaisesRegex(common.Failure, 'malformed'):
            _ = common.command_json([bad], self.root, 2)

    def test_root_and_missing_provider_failures(self) -> None:
        _ = self.write('flake.nix', '{}')
        nested = self.root / 'nested'
        nested.mkdir()
        self.assertEqual(main.find_root(nested, 'flake.nix'), self.root)
        with self.assertRaises(common.Failure):
            _ = common.relative(self.root, '../outside')
        config: main.Inventory = {'providers': {'uv': {'root': 'missing'}}, 'tools': [], 'releases': [], 'adapters': []}
        work = main.jobs(config, self.root, network.Client())
        self.assertEqual(main.guarded(*work[0])[0].state, 'error')


class Releases(Fixture):
    def test_unreadable_versions_skip_without_package_exceptions(self) -> None:
        client = network.Client()
        for latest in ('php-8.5.1', 'cli/v2.0', 'snapshot', '3.0rc1'):
            with patch.object(releases, 'lookup', return_value=latest):
                rows = main.guarded('tools', 'fixture', '.', lambda: client.release('owner/repo'))
            self.assertEqual(rows[0].state, 'skipped')
            self.assertEqual(common.summary(rows)[1], 0)
            with patch.object(releases, 'lookup', return_value=latest):
                row = sources.release_entry(client, {'name': 'fixture', 'repo': 'owner/repo', 'version': '1.0'})
            self.assertEqual((row.state, row.current), ('skipped', '1.0'))

    def test_nvchecker_events_errors_and_no_result(self) -> None:
        for event in ('updated', 'up-to-date'):
            data = json.dumps({'event': event, 'name': 'entry', 'version': '2.0'})
            with patch.object(releases, 'run', return_value=(data, '')):
                self.assertEqual(releases.lookup('nvchecker', {}, 2), '2.0')
        no_result = {'event': 'no-result', 'level': 'error', 'name': 'entry'}
        with (
            patch.object(releases, 'run', return_value=(json.dumps(no_result), '')),
            self.assertRaises(common.UnreadableSource),
        ):
            _ = releases.lookup('nvchecker', {}, 2)
        for events in (
            [],
            [None],
            [no_result, no_result],
            [{'event': 'updated', 'name': 'wrong', 'version': '2'}],
            [{'event': 'updated', 'name': 'entry', 'version': None}],
            [{'event': 'updated', 'name': 'entry', 'version': ''}],
            [{'event': 'private-secret', 'level': 'error'}, no_result],
            [{'event': 'private-secret', 'level': 'error'}, {'event': 'updated', 'name': 'entry', 'version': '2'}],
        ):
            with (
                self.subTest(events=events),
                patch.object(releases, 'run', return_value=('\n'.join(map(json.dumps, events)), '')),
            ):
                rows = main.guarded('tools', 'fixture', '.', lambda: releases.lookup('nvchecker', {}, 2))
                self.assertEqual(rows[0].state, 'error')
                self.assertNotIn('private-secret', rows[0].detail)
        with patch.object(releases, 'run', return_value=('broken JSON', '')), self.assertRaises(common.Failure):
            _ = releases.lookup('nvchecker', {}, 2)

    def test_runtime_config_is_private_temporary_and_not_in_argv(self) -> None:
        import tomllib

        paths: list[pathlib.Path] = []

        def execute(argv: Sequence[str], _root: pathlib.Path, _timeout: float, **_kwargs: object) -> tuple[str, str]:
            path = pathlib.Path(argv[2])
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            config = cast(dict[str, dict[str, str]], tomllib.loads(path.read_text()))
            self.assertEqual(config['entry']['token'], 'private-secret')
            self.assertFalse('oldver' in config['__config__'] or 'newver' in config['__config__'])
            self.assertNotIn('private-secret', str(argv))
            return json.dumps({'event': 'updated', 'name': 'entry', 'version': 'v2.0'}), ''

        with patch.dict(os.environ, {'GH_TOKEN': 'private-secret'}), patch.object(releases, 'run', side_effect=execute):
            self.assertEqual(network.Client().release('owner/repo'), ('v2.0', 'v2.0'))
        self.assertFalse(paths[0].exists())

    def test_network_failure_is_not_a_tag_fallback(self) -> None:
        client = network.Client()
        with (
            patch.object(releases, 'lookup', side_effect=common.Failure('Lookup failed')) as lookup,
            self.assertRaises(common.Failure),
        ):
            _ = client.release('owner/repo')
        self.assertEqual(lookup.call_count, 1)
        with self.assertRaises(common.Failure):
            _ = client.release('https://secret@evil/repo')
        with patch.object(releases, 'lookup') as lookup, self.assertRaises(common.UnreadableSource):
            _ = network.Client(github='https://enterprise.example/api').release('owner/repo')
        lookup.assert_not_called()

    def test_registry_release_dispatch(self) -> None:
        client = network.Client()
        client.registry_release = Mock(return_value='1.0')
        row = sources.release_entry(client, {'name': 'thing', 'provider': 'pypi', 'project': 'thing', 'version': '1.0'})
        self.assertEqual(row.state, 'up-to-date')
        self.assertEqual(row.version_url, 'https://pypi.org/project/thing/1.0/')
        self.assertEqual(row.current_url, 'https://pypi.org/project/thing/1.0/')
        self.assertEqual(
            list(row.json()),
            ['provider', 'project', 'name', 'source', 'state', 'current', 'compatible', 'latest', 'detail'],
        )

    def test_release_links_follow_verified_source_and_policy(self) -> None:
        client = network.Client()
        client.release = Mock(return_value=('v2.0+stable', '2.0'))
        release = sources.release_entry(client, {'name': 'tool', 'repo': 'owner/repo', 'version': '1.0'})
        self.assertEqual(release.version_url, 'https://github.com/owner/repo/releases/tag/v2.0%2Bstable')
        self.assertEqual(release.current_url, '')
        tagged = sources.release_entry(client, {'name': 'tool', 'repo': 'owner/repo', 'version': '1.0', 'tags': True})
        self.assertEqual(tagged.version_url, 'https://github.com/owner/repo/tree/v2.0%2Bstable')
        client.commit = Mock(return_value='b' * 40)
        action = sources.action('owner/repo@v1', 'workflow.yml', client)
        self.assertEqual(action.version_url, 'https://github.com/owner/repo/releases/tag/v2.0%2Bstable')
        for kind, project, expected in (
            ('npm', '@scope/tool', 'https://www.npmjs.com/package/@scope/tool/v/2.0'),
            ('crates', 'tool', 'https://crates.io/crates/tool/2.0'),
        ):
            with self.subTest(kind=kind), patch.object(sources, 'command_json') as compare_report:
                compare_report.return_value = {
                    'schemaVersion': 1,
                    'results': [{'name': project, 'state': 'outdated', 'current': '1.0', 'latest': '2.0'}],
                }
                client.registry_release = Mock(return_value='2.0')
                row = sources.release_entry(
                    client,
                    {'name': 'tool', 'provider': kind, 'project': project, 'version': '1.0', 'reporter': 'report'},
                )
                self.assertEqual(row.version_url, expected)
                self.assertIn('/1.0', row.current_url)

    def test_nix_follows_and_owners(self) -> None:
        root_inputs: dict[str, object] = {'tools': 'tools', 'pkgs': 'pkgs'}
        lock: sources.NixLock = {
            'root': 'root',
            'nodes': {
                'root': {'inputs': root_inputs},
                'tools': {'inputs': {'pkgs': ['pkgs']}, 'locked': {}},
                'pkgs': {'locked': {}},
            },
        }
        self.assertEqual(sources.input_owners(lock)['pkgs'], {'pkgs', 'tools/pkgs'})
        root_inputs['cycle'] = ['cycle']
        with self.assertRaises(common.Failure):
            _ = sources.input_owners(lock)

    def test_nix_pins_branches_and_releases(self) -> None:
        client = network.Client()
        client.release = Mock(return_value=('v2.0', '2.0'))
        client.commit = Mock(return_value='b' * 40)
        node: sources.NixNode = {
            'locked': {'type': 'github', 'owner': 'o', 'repo': 'r', 'rev': 'a' * 40},
            'original': {'ref': 'v1.0'},
        }
        row = sources.nix_input(node, 'tools/dependency', {}, self.root, client)
        self.assertEqual(row.state, 'outdated')
        self.assertEqual(row.version_url, 'https://github.com/o/r/tree/v2.0')
        self.assertEqual(row.current_url, 'https://github.com/o/r/commit/' + 'a' * 40)
        self.assertEqual(row.detail_identifiers, ('o/r', 'tools'))
        self.assertIn('owning top-level input: tools', row.detail)
        client.release.assert_called_once_with('o/r', tags=True)
        client.commit.assert_called_with('o/r', 'v2.0')
        client.release.side_effect = common.UnreadableSource('No readable release')
        skipped = sources.nix_input(node, 'tools', {}, self.root, client)
        self.assertEqual((skipped.state, skipped.current), ('skipped', 'a' * 40))
        skipped = sources.action('o/r@v1.0', 'workflow.yml', client)
        self.assertEqual((skipped.state, skipped.current), ('skipped', 'v1.0'))
        node['original'] = {'rev': 'a' * 40}
        pinned = sources.nix_input(node, 'tools', {}, self.root, client)
        self.assertEqual(pinned.state, 'pinned')
        self.assertEqual(pinned.version_url, 'https://github.com/o/r/commit/' + 'a' * 40)
        node['original'] = {'ref': 'main'}
        branch = sources.nix_input(node, 'tools', {}, self.root, client)
        self.assertEqual(branch.version_url, 'https://github.com/o/r/commit/' + 'b' * 40)
        node['locked']['type'] = 'path'
        node['original'] = {}
        self.assertEqual(sources.nix_input(node, 'tools', {}, self.root, client).state, 'unknown')

    def test_workflows_subpaths_reusable_and_local(self) -> None:
        _ = self.write(
            '.github/workflows/test.yml',
            """jobs:
  reuse:
    uses: owner/repo/.github/workflows/test.yml@v1
  build:
    steps:
      - uses: owner/repo/subpath@v1
      - uses: ./local
      - uses: docker://image:latest
      - uses: ${{ matrix.action }}
""",
        )
        client = network.Client()
        client.release = Mock(return_value=('v1.2.0', '1.2.0'))
        client.commit = Mock(return_value='a' * 40)
        work = list(sources.workflow_jobs({}, self.root, client))
        rows = [callback() for _, callback in work]
        self.assertEqual(Counter(row.state for row in rows), {'unknown': 2, 'skipped': 1, 'up-to-date': 2})


class Native(Fixture):
    def test_uv_lock_uses_declared_registry_and_marks_other_sources(self) -> None:
        _ = self.write('pyproject.toml', '[project]\nname="test"\n')
        _ = self.write(
            'uv.lock',
            """[[package]]
name="pkg"
version="1.0"
source={registry="https://private.example/simple"}
[[package]]
name="local"
version="0.0"
source={virtual="."}
[[package]]
name="git"
version="1.0"
source={git="https://example.test/repo"}
""",
        )
        response: uv.Document = {
            'schema': {'version': 'preview'},
            'resolution': {
                'p': {
                    'kind': 'package',
                    'name': 'pkg',
                    'version': '1.0',
                    'source': {'registry': {'url': 'https://private.example/simple'}},
                    'latest_version': '2.0',
                },
                'local': {'kind': 'package', 'name': 'local', 'version': '0.0', 'source': {'virtual': '.'}},
                'git': {'kind': 'package', 'name': 'git', 'version': '1.0', 'source': {'git': {}}},
            },
        }
        with patch.object(uv, 'command_json', return_value=response) as command:
            rows = list(uv.report({'exe': 'uv'}, self.root, 2))
        self.assertEqual([r.state for r in rows], ['outdated', 'skipped', 'unknown'])
        self.assertIn('--locked', cast(list[str], command.call_args.args[0]))
        self.assertEqual(rows[0].version_url, '')
        response['resolution']['p']['source']['registry'] = {'url': 'https://pypi.org/simple'}
        with patch.object(uv, 'command_json', return_value=response):
            rows = list(uv.report({'exe': 'uv'}, self.root, 2))
        self.assertEqual(rows[0].version_url, 'https://pypi.org/project/pkg/2.0/')
        self.assertEqual(rows[0].current_url, 'https://pypi.org/project/pkg/1.0/')
        _ = response['resolution']['p'].pop('latest_version')
        with patch.object(uv, 'command_json', return_value=response):
            rows = list(uv.report({'exe': 'uv'}, self.root, 2))
            self.assertEqual((rows[0].state, rows[0].current), ('unknown', '1.0'))
            self.assertEqual(common.summary(rows)[1], 2)

    def test_cargo_workspace_compatibility(self) -> None:
        _ = self.write('Cargo.toml', 'native-owned')
        _ = self.write('Cargo.lock', 'native-owned')
        document = {'dependencies': [{'name': 'pkg', 'project': '1.0.0', 'compat': '1.1.0', 'latest': '2.0.0'}]}
        with patch.object(cargo, 'command_json', side_effect=[{'packages': []}, document]) as command:
            rows = list(cargo.report({'exe': 'cargo-outdated', 'cargo': 'cargo'}, self.root, 2))
        self.assertEqual(rows[0].compatible, '1.1.0')
        self.assertIn('--locked', cast(list[str], command.call_args_list[0].args[0]))
        self.assertNotEqual(command.call_args.args[1], self.root)

    def test_composer_locked_and_pnpm_workspaces(self) -> None:
        _ = self.write('composer.json', 'native-owned')
        _ = self.write('composer.lock', 'native-owned')
        with patch.object(
            composer,
            'command_json',
            return_value={
                'locked': [
                    {'name': 'pkg/name', 'version': '1.0.0', 'latest': '2.0.0', 'latest-status': 'update-possible'},
                    {'name': 'current', 'version': '1.0.0', 'latest': '1.0.0', 'latest-status': 'up-to-date'},
                ]
            },
        ) as command:
            rows = list(composer.report({'exe': 'composer'}, self.root, 2))
        self.assertEqual([row.state for row in rows], ['outdated', 'up-to-date'])
        for flag in ('--no-plugins', '--locked', '--all'):
            self.assertIn(flag, cast(list[str], command.call_args.args[0]))
        _ = self.write('package.json', 'native-owned')
        _ = self.write('pnpm-lock.yaml', 'native-owned')

        def report(args: Sequence[str], directory: pathlib.Path, _timeout: float, **_kwargs: object) -> object:
            if 'list' in args:
                return [
                    {
                        'path': str(directory),
                        'dependencies': {'sibling': {'version': 'link:../sibling'}, 'pkg': {'version': '1.0.0'}},
                    }
                ]
            if 'pkg' in args and 'get' in args:
                return {'dependencies': {'pkg': '^1.0.0'}}
            if 'outdated' in args:
                return {'pkg': {'current': '1.0.0', 'wanted': '1.1.0', 'latest': '2.0.0'}}
            self.assertEqual(json.loads(pathlib.Path(args[-1]).read_text())[0]['compatible'], '1.1.0')
            return {'schemaVersion': 1, 'results': [{'name': 'pkg', 'state': 'outdated'}]}

        with patch.object(pnpm, 'command_json', side_effect=report):
            rows = list(pnpm.report({'exe': 'pnpm', 'semver': 'versions'}, self.root, 2))
        self.assertEqual([r.state for r in rows], ['skipped', 'outdated'])

    def test_npm_inventory_comes_from_lock_not_install(self) -> None:
        _ = self.write('package.json', 'native-owned')
        _ = self.write('package-lock.json', 'native-owned')
        document = {
            'schemaVersion': 1,
            'results': [
                {
                    'name': 'pkg',
                    'source': 'package-lock.json:node_modules/pkg',
                    'current': '1.0.0',
                    'state': 'outdated',
                },
                {
                    'name': 'pkg',
                    'source': 'package-lock.json:node_modules/other/node_modules/pkg',
                    'current': '1.1.0',
                    'state': 'outdated',
                },
            ],
        }
        with patch.object(npm, 'command_json', return_value=document) as command:
            rows = list(npm.report({'exe': sys.executable, 'reporter': 'report'}, self.root, 2))
        self.assertEqual({r.current for r in rows}, {'1.0.0', '1.1.0'})
        self.assertEqual(len({r.source for r in rows}), 2)
        self.assertEqual(command.call_args.args[0], ['report', 'npm', sys.executable])

    def test_yarn_consumes_native_report_without_reading_lock(self) -> None:
        _ = self.write('package.json', 'native-owned')
        _ = self.write('yarn.lock', 'native-owned')
        document = {
            'schemaVersion': 1,
            'results': [
                {'name': 'root', 'state': 'skipped'},
                {'name': 'pkg', 'current': '1.0.0', 'latest': '2.0.0', 'compatible': '1.2.0', 'state': 'outdated'},
                {'name': 'patched', 'state': 'unknown'},
            ],
        }
        with (
            patch.object(yarn, 'run') as setup,
            patch.object(yarn, 'command_json', return_value=document) as command,
        ):
            rows = list(yarn.report({'exe': 'yarn'}, self.root, 2))
        self.assertEqual([r.state for r in rows], ['skipped', 'outdated', 'unknown'])
        self.assertEqual(rows[1].compatible, '1.2.0')
        self.assertEqual(setup.call_args.args[0][1:3], ['plugin', 'import'])
        self.assertEqual(command.call_args.args[0], ['yarn', 'nix-tools-outdated'])

    def test_native_failure_cannot_mutate_source(self) -> None:
        _ = self.js('package.json', {})
        lock = self.js(
            'package-lock.json',
            {
                'lockfileVersion': 3,
                'packages': {'node_modules/pkg': {'version': '1.0.0', 'resolved': 'https://registry/pkg.tgz'}},
            },
        )
        original = lock.read_bytes()
        tool = self.tool('import pathlib,sys\npathlib.Path("package-lock.json").write_text("broken")\nsys.exit(1)\n')
        with self.assertRaises(common.Failure):
            _ = list(npm.report({'exe': tool, 'reporter': tool}, self.root, 2))
        self.assertEqual(lock.read_bytes(), original)


class EdgeCases(Fixture):
    def test_all_unknown_protocols_remain_nonzero(self) -> None:
        for state in ('unknown', 'blocked', 'error'):
            with self.subTest(state=state), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.report([common.Result('p', 'x', '.', state)], True), 2)

    def test_invalid_result_fields_do_not_crash_report(self) -> None:
        # Deliberately violate the annotation to exercise runtime protocol validation.
        for row in (
            common.Result('p', 'x', '.', 'invalid'),
            common.Result('p', 'x', '.', 'skipped', cast(str, cast(object, None))),
        ):
            self.assertEqual(main.guarded('p', 'x', '.', lambda row=row: row)[0].state, 'error')

    def test_redirects_never_forward_credentials_across_origins(self) -> None:
        handler = network.SafeRedirect()
        request = urllib.request.Request('https://api.example/a', headers={'Authorization': 'Bearer secret'})
        for target in ('http://api.example/b', 'https://other.example/b', 'https://api.example:123/b'):
            with self.subTest(target=target), self.assertRaises(common.Failure):
                _ = handler.redirect_request(request, io.BytesIO(), 302, 'redirect', HTTPMessage(), target)
        new = handler.redirect_request(request, io.BytesIO(), 302, 'redirect', HTTPMessage(), 'https://api.example/b')
        assert new is not None
        self.assertEqual(new.get_header('Authorization'), 'Bearer secret')

    def test_http_auth_and_failure_redaction(self) -> None:
        response = MagicMock(__enter__=Mock(return_value=io.StringIO('{"ok": true}')))
        open_request = Mock(return_value=response)
        opener = Mock(open=open_request)
        with (
            patch.dict(os.environ, {'GH_TOKEN': 'secret-value'}),
            patch.object(urllib.request, 'build_opener', return_value=opener),
        ):
            client = network.Client()
            self.assertEqual(client.api('test'), {'ok': True})
            self.assertEqual(
                cast(urllib.request.Request, open_request.call_args.args[0]).get_header('Authorization'),
                'Bearer secret-value',
            )
            open_request.return_value = MagicMock(__enter__=Mock(return_value=io.StringIO('{}')))
            _ = client.get('https://pypi.org/test')
            self.assertIsNone(cast(urllib.request.Request, open_request.call_args.args[0]).get_header('Authorization'))
            open_request.side_effect = urllib.error.HTTPError('https://secret@example', 429, 'private', Message(), None)
            with self.assertRaisesRegex(common.Failure, 'HTTP 429') as caught:
                _ = client.api('limited')
            self.assertNotIn('secret', str(caught.exception))

    def test_github_quota_preflight_stops_repeated_lookups(self) -> None:
        client = network.Client(preflight=True)
        quota = {'resources': {'core': {'remaining': 0, 'reset': 1791025832}}}
        with (
            patch.object(client, '_get', return_value=quota) as request,
            patch.object(network, 'source') as release,
        ):
            for call in (lambda: client.release('owner/repo'), lambda: client.api('repos/owner/repo')):
                with self.assertRaisesRegex(common.Failure, 'GitHub API quota exhausted') as caught:
                    _ = call()
                self.assertIn('GH_TOKEN or GITHUB_TOKEN', str(caught.exception))
            self.assertEqual(request.call_count, 1)
            release.assert_not_called()

    def test_github_quota_preflight_allows_available_quota(self) -> None:
        client = network.Client(preflight=True)
        quota = {'resources': {'core': {'remaining': 4, 'reset': 1791025832}}}
        with (
            patch.object(client, '_get', return_value=quota) as request,
            patch.object(network, 'source', return_value=('v2', '2')) as release,
        ):
            self.assertEqual(client.release('owner/repo'), ('v2', '2'))
            self.assertEqual(client.release('owner/repo'), ('v2', '2'))
            request.assert_called_once_with('https://api.github.com/rate_limit', github=True)
            self.assertEqual(release.call_count, 2)

    def test_github_quota_preflight_failure_does_not_block_release(self) -> None:
        documents: tuple[object, ...] = (
            [],
            {'resources': []},
            {'resources': {'core': []}},
            {'resources': {'core': {'remaining': False}}},
        )
        for document in documents:
            with self.subTest(document=document):
                client = network.Client(preflight=True)
                with (
                    patch.object(client, '_get', return_value=document) as probe,
                    patch.object(network, 'source', return_value=('v2', '2')) as release,
                ):
                    self.assertEqual(client.release('owner/repo'), ('v2', '2'))
                    self.assertEqual(client.release('owner/repo'), ('v2', '2'))
                    probe.assert_called_once()
                    self.assertEqual(release.call_count, 2)
        client = network.Client(preflight=True)
        with (
            patch.object(client, '_get', side_effect=common.Failure('Probe unavailable')) as probe,
            patch.object(network, 'source', return_value=('v2', '2')) as release,
        ):
            self.assertEqual(client.release('owner/repo'), ('v2', '2'))
            self.assertEqual(client.release('owner/repo'), ('v2', '2'))
            probe.assert_called_once()
            self.assertEqual(release.call_count, 2)
        forbidden = urllib.error.HTTPError('https://api.github.com/test', 403, 'Forbidden', Message(), None)
        with (
            patch.object(urllib.request, 'build_opener', return_value=Mock(open=Mock(side_effect=forbidden))),
            self.assertRaisesRegex(common.Failure, 'HTTP 403'),
        ):
            _ = network.Client().api('test')

    def test_github_quota_preflight_preserves_confirmed_access_failures(self) -> None:
        for code, headers, diagnostic in (
            (403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1791025832'}, 'quota exhausted'),
            (401, {}, 'authentication failed'),
        ):
            with self.subTest(code=code):
                client = network.Client(preflight=True)
                response_headers = Message()
                for key, value in headers.items():
                    response_headers[key] = value
                failure = urllib.error.HTTPError('https://api.github.com/rate_limit', code, '', response_headers, None)
                open_request = Mock(side_effect=failure)
                with (
                    patch.object(urllib.request, 'build_opener', return_value=Mock(open=open_request)),
                    patch.object(network, 'source') as release,
                ):
                    for call, target in (
                        (client.release, 'owner/repo'),
                        (client.api, 'repos/owner/repo'),
                    ):
                        with self.assertRaisesRegex(common.Failure, diagnostic):
                            _ = call(target)
                    open_request.assert_called_once()
                    release.assert_not_called()

    def test_github_rate_limit_headers_are_actionable_and_safe(self) -> None:
        headers = Message()
        headers['x-ratelimit-remaining'] = '0'
        headers['x-ratelimit-reset'] = '1791025832'
        opener = Mock(
            open=Mock(side_effect=urllib.error.HTTPError('https://secret@example', 403, 'private', headers, None))
        )
        with (
            patch.object(urllib.request, 'build_opener', return_value=opener),
            self.assertRaisesRegex(common.Failure, 'GitHub API quota exhausted') as caught,
        ):
            _ = network.Client().api('repos/owner/repo')
        self.assertNotIn('secret', str(caught.exception))
        self.assertIn('UTC', str(caught.exception))

    def test_rich_table_keeps_rows_and_deduplicates_finding_details(self) -> None:
        rows = [
            common.Result('githubActions', name, '.', 'error', detail='GitHub API quota exhausted')
            for name in ('one', 'two', 'three')
        ]
        with contextlib.redirect_stdout(io.StringIO()) as plain:
            self.assertEqual(main.report(rows, False), 2)
        for value in ('one', 'two', 'three', 'github-api-quota', 'OUTDATED: ERROR'):
            self.assertIn(value, plain.getvalue())
        self.assertNotIn('│', plain.getvalue())
        self.assertEqual(plain.getvalue().count('GitHub API quota exhausted'), 1)
        with contextlib.redirect_stdout(io.StringIO()) as machine:
            self.assertEqual(main.report(rows, True), 2)
        self.assertEqual(len(cast(dict[str, list[object]], json.loads(machine.getvalue()))['results']), 3)

    def test_redirected_report_is_terse_without_hiding_skips_or_compatibility(self) -> None:
        rows = [
            common.Result('tools', 'current-tool', '.', 'up-to-date', '1.0'),
            common.Result('tools', 'local-tool', '.', 'skipped', '1.0', detail='Local package'),
            common.Result('tools', 'pinned-tool', '.', 'pinned', '1.0', detail='Explicit revision pin'),
            common.Result('tools', 'old-tool', '.', 'outdated', '1.0', '1.5', '2.0', 'New release'),
        ]
        with contextlib.redirect_stdout(io.StringIO()) as plain:
            self.assertEqual(main.report(rows, False), 1)
        self.assertEqual(
            plain.getvalue().rstrip('\n').splitlines()[-1],
            'OUTDATED: OUTDATED (1 outdated, 1 pinned, 1 skipped, 1 up-to-date)',
        )
        self.assertIn('\n\nOUTDATED: OUTDATED (', plain.getvalue())
        self.assertTrue(plain.getvalue().endswith(')\n\n'))
        self.assertIn('old-tool 1.0 → 2.0 (compatible 1.5)', plain.getvalue())
        self.assertNotIn('current-tool', plain.getvalue())
        self.assertIn('local-tool', plain.getvalue())
        self.assertIn('pinned-tool', plain.getvalue())
        self.assertIn('local-package: Local package', plain.getvalue())
        self.assertIn('[new-release]', plain.getvalue())
        self.assertIn('new-release: New release', plain.getvalue())
        self.assertNotIn('\x1b', plain.getvalue())
        adapter = common.Result(
            'adapter:application', 'runtime', '.', 'outdated', '1.0', latest='2.0', detail='Upgrade breaks target CPU'
        )
        with contextlib.redirect_stdout(io.StringIO()) as policy:
            self.assertEqual(main.report([adapter], False), 1)
        self.assertIn('upgrade-breaks-target: Upgrade breaks target CPU', policy.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as machine:
            self.assertEqual(main.report(rows, True), 1)
        self.assertEqual(len(cast(dict[str, list[object]], json.loads(machine.getvalue()))['results']), 4)

    def test_terse_adapter_fields_cannot_forge_a_verdict_line(self) -> None:
        forged = 'OUTDATED: UP-TO-DATE (1 up-to-date)'
        document = {
            'schemaVersion': 1,
            'results': [
                {
                    'name': f'entry\n{forged}',
                    'state': 'error',
                    'current': f'1\n{forged}',
                    'compatible': f'1.5\n{forged}',
                    'latest': f'2\n{forged}',
                    'detail': f'Lookup failed\n{forged}',
                }
            ],
        }
        rows = common.validate_adapter(document, 'adapter:fixture', '.')
        with contextlib.redirect_stdout(io.StringIO()) as plain:
            self.assertEqual(main.report(rows, False), 2)
        lines = plain.getvalue().splitlines()
        self.assertEqual([line for line in lines if line.startswith('OUTDATED:')], ['OUTDATED: ERROR (1 error)'])
        self.assertEqual(plain.getvalue().rstrip('\n').splitlines()[-1], 'OUTDATED: ERROR (1 error)')
        self.assertIn('\n\nOUTDATED: ERROR (1 error)\n\n', plain.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as machine:
            self.assertEqual(main.report(rows, True), 2)
        self.assertEqual(json.loads(machine.getvalue())['results'][0]['current'], f'1\n{forged}')

    def test_note_codes_are_readable_and_disambiguate_similar_explanations(self) -> None:
        used: set[str] = set()
        self.assertEqual(main.note_code('GitHub API quota exhausted; retry later', used), 'github-api-quota')
        self.assertEqual(main.note_code('NixOS/nixpkgs: branch moved', used), 'nixos-nixpkgs')
        self.assertEqual(main.note_code('NixOS/nixpkgs: another branch moved', used), 'nixos-nixpkgs-2')
        self.assertEqual(main.note_code('UV did not report a version; source failed', used), 'uv-did-not-report')

    def test_finding_details_style_known_identifiers_without_changing_json(self) -> None:
        detail = 'NixOS/nixpkgs: main -> stable. Update the owning top-level input: nix-gritql, uv2nix'
        identifiers = ('NixOS/nixpkgs', 'nix-gritql', 'uv2nix')
        styled = main.styled_detail(detail, identifiers)
        self.assertEqual(
            {styled.plain[span.start : span.end] for span in styled.spans},
            set(identifiers),
        )
        assert main.IDENTIFIER_STYLE.color is not None
        self.assertEqual(main.IDENTIFIER_STYLE.color.name, 'bright_white')
        row = common.Result('nix', 'nix-gritql', '.', 'outdated', detail=detail, detail_identifiers=identifiers)
        self.assertNotIn('detail_identifiers', row.json())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.report([row], True), 1)
        self.assertEqual(json.loads(output.getvalue())['results'][0]['detail'], detail)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.report([row], False), 1)
        self.assertLess(max(map(len, output.getvalue().splitlines())), 120)

        with patch.dict(os.environ, {'TERM': 'xterm-256color', 'COLUMNS': '160'}, clear=True):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
        self.assertIn('Finding details', output.getvalue())
        self.assertIn('NixOS/nixpkgs', output.getvalue())

        routine = common.Result('uv', 'package', '.', 'outdated', '1', latest='2', detail='Upstream availability')
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=True):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([routine], False), 1)
        self.assertIn('Finding details', output.getvalue())
        self.assertIn('upstream-availability', output.getvalue())
        self.assertIn('Upstream availability', output.getvalue())

    def test_rich_table_compacts_nix_paths_and_revisions_but_json_retains_them(self) -> None:
        first = 'nix-tools/pyproject-build-systems/nixpkgs'
        label = f'{first}, nix-tools/uv2nix/nixpkgs, uv2nix/nixpkgs'
        revision = 'a' * 40
        row = common.Result('nix', label, 'flake.lock', 'up-to-date', revision, latest=revision, detail='Done')

        with (
            patch.dict(os.environ, {'TERM': 'xterm-256color', 'COLUMNS': '160'}, clear=True),
            contextlib.redirect_stdout(Terminal()) as plain,
        ):
            self.assertEqual(main.report([row], False), 0)
        self.assertIn('(+2 paths)', plain.getvalue())
        self.assertIn('a' * 10, plain.getvalue())
        self.assertNotIn(revision, plain.getvalue())
        self.assertNotIn('Done', plain.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as machine:
            self.assertEqual(main.report([row], True), 0)
        self.assertEqual(json.loads(machine.getvalue())['results'][0]['name'], label)
        self.assertEqual(json.loads(machine.getvalue())['results'][0]['current'], revision)

    def test_rich_table_stripes_tty_rows_without_interpreting_names_as_markup(self) -> None:
        rows = [
            common.Result('tools', '[red]literal[/red]', '.', 'outdated', '1', latest='2'),
            common.Result('tools', 'second', '.', 'up-to-date', '2', latest='2'),
        ]
        output = Terminal()
        with patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(main.report(rows, False), 1)
        self.assertIn('[red]literal[/red]', output.getvalue())
        self.assertIn('\x1b[48', output.getvalue())

    def test_rich_tables_use_content_width_and_terminal_only_release_links(self) -> None:
        url = 'https://github.com/owner/repo/releases/tag/v2.0'
        old_url = 'https://github.com/owner/repo/releases/tag/v1.0'
        row = common.Result(
            'releases', 'tool', '.', 'outdated', '1.0', latest='2.0', version_url=url, current_url=old_url
        )
        cell = main.version_cell(row, True)
        self.assertEqual(cell.plain, '1.0 → 2.0')
        self.assertEqual(
            [(cell.plain[s.start : s.end], s.style.link) for s in cell.spans if isinstance(s.style, Style)],
            [('1.0', old_url), ('2.0', url)],
        )
        with patch.dict(os.environ, {'TERM': 'xterm-256color', 'COLUMNS': '160'}, clear=True):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertIn(';' + old_url + '\x1b\\', output.getvalue())
            self.assertIn(';' + url + '\x1b\\', output.getvalue())
            self.assertIn('│', output.getvalue())
            self.assertIn('\n\x1b[1mOUTDATED:', output.getvalue())
            self.assertTrue(output.getvalue().endswith('\n\n'))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertNotIn('\x1b]8;;', output.getvalue())
            self.assertNotIn(url, output.getvalue())
            self.assertNotIn('│', output.getvalue())
            self.assertLess(max(map(len, output.getvalue().splitlines())), 100)
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], True), 1)
            self.assertNotIn('\x1b]8;;', output.getvalue())
            self.assertNotIn('version_url', output.getvalue())
            output = Terminal()
            with patch.dict(os.environ, {'CI': '1'}), contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertNotIn('\x1b]8;;', output.getvalue())
            output = Terminal()
            with patch.dict(os.environ, {'NO_COLOR': '1'}), contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertNotIn('\x1b', output.getvalue())
            output = Terminal()
            with patch.dict(os.environ, {'NO_COLOR': '1', 'FORCE_COLOR': '1'}), contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 1)
            self.assertIn(';' + url + '\x1b\\', output.getvalue())
        no_old = common.Result('releases', 'tool', '.', 'outdated', '1.0', latest='2.0', version_url=url)
        cell = main.version_cell(no_old, True)
        self.assertEqual(
            [(cell.plain[s.start : s.end], s.style.link) for s in cell.spans if isinstance(s.style, Style)],
            [('2.0', url)],
        )

    def test_narrow_terminal_keeps_state_readable(self) -> None:
        row = common.Result('nix', 'long-repository-name/long-input-name', '.', 'up-to-date', 'a' * 40)
        with patch.dict(os.environ, {'TERM': 'xterm-256color', 'COLUMNS': '80'}, clear=True):
            output = Terminal()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main.report([row], False), 0)
        self.assertIn('up-to-date', output.getvalue())

    def test_progress_uses_completed_jobs_but_preserves_report_order(self) -> None:
        release_first = threading.Event()

        def first():
            if not release_first.wait(2):
                raise AssertionError('The first job blocked progress')
            return common.Result('test', 'first', '.', 'up-to-date')

        class ProgressProbe:
            started: bool
            calls: list[tuple[object, ...]]

            def __init__(self) -> None:
                self.started = False
                self.calls = []

            def __enter__(self) -> Self:
                self.started = True
                return self

            def __exit__(self, *_args: object) -> None:
                self.started = False

            def add_task(self, description: str, total: int | None) -> int:
                self.calls.append(('add', description, total))
                return 7

            def update(self, task: int, **kwargs: object) -> None:
                self.calls.append(('update', task, kwargs))

            def advance(self, task: int) -> None:
                self.calls.append(('advance', task))
                if sum(call[0] == 'advance' for call in self.calls) == 1:
                    release_first.set()

        probe = ProgressProbe()
        jobs: list[main.Job] = [
            ('test', 'first', '.', first),
            ('test', 'second', '.', lambda: common.Result('test', 'second', '.', 'up-to-date')),
        ]

        def inventory(*_args: object) -> list[main.Job]:
            self.assertTrue(probe.started)
            self.assertEqual(probe.calls[0], ('add', 'Discovering inputs', None))
            return jobs

        with (
            patch.object(main, 'progress_display', return_value=probe),
            patch.object(main, 'jobs', side_effect=inventory),
        ):
            rows = main.collect_jobs(
                {'providers': {}, 'tools': [], 'releases': [], 'adapters': [], 'concurrency': 2},
                self.root,
                Mock(),
                Mock(),
            )
        self.assertEqual([row.name for row in rows], ['first', 'second'])
        self.assertEqual(
            probe.calls[1], ('update', 7, {'description': 'Checking dependencies', 'total': 2, 'completed': 0})
        )
        self.assertEqual(probe.calls[2:], [('advance', 7), ('advance', 7)])

    def test_release_policy_custom_patterns_and_empty(self) -> None:
        client = network.Client()
        with patch.object(releases, 'lookup', return_value='cli/v2.0'):
            self.assertEqual(
                client.release('o/r', tags=True, tagPattern=r'cli/v(?P<version>\d+\.\d+)'), ('cli/v2.0', '2.0')
            )
        client.api = Mock(return_value={'sha': 'not-a-commit'})
        with self.assertRaises(common.Failure):
            _ = client.commit('o/r', 'a/b')

    def test_git_tags_and_annotated_nix_tags(self) -> None:
        client = network.Client(timeout=2)
        client.registry_release = Mock(return_value='2.0')
        row = sources.release_entry(
            client,
            {'name': 'git', 'version': '1.0', 'provider': 'git', 'url': 'https://example.test/r'},
        )
        self.assertEqual(row.latest, '2.0')
        node: sources.NixNode = {
            'locked': {'type': 'git', 'rev': 'b', 'url': 'https://example.test/r'},
            'original': {'ref': 'v1'},
        }
        with patch.object(sources, 'run', return_value=('a\trefs/tags/v1\nb\trefs/tags/v1^{}\n', '')):
            self.assertEqual(sources.nix_input(node, 'n', {'git': 'git'}, self.root, client).state, 'up-to-date')
        with patch.object(sources, 'run', return_value=('', '')), self.assertRaises(common.Failure):
            _ = sources.nix_input(node, 'n', {'git': 'git'}, self.root, client)

    def test_native_empty_and_invalid_schemas(self) -> None:
        for name in ('package.json', 'composer.json', 'composer.lock', 'package-lock.json', 'pnpm-lock.yaml'):
            _ = self.write(name, 'native-owned')
        with patch.object(composer, 'command_json', return_value={'locked': []}):
            self.assertEqual(
                next(composer.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 2)).state, 'up-to-date'
            )
        with patch.object(npm, 'command_json', return_value={'schemaVersion': 1, 'results': []}):
            self.assertEqual(
                next(npm.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 2)).state, 'up-to-date'
            )
        for provider in ('npm', 'composer', 'pnpm'):
            documents: tuple[object, ...] = ({}, [], {'schemaVersion': 1, 'results': [{'state': 'up-to-date'}]})
            for document in documents:
                with (
                    self.subTest(provider=provider, document=document),
                    patch.object(sys.modules[PROVIDERS[provider].__module__], 'command_json', return_value=document),
                ):
                    rows = main.guarded(
                        provider,
                        provider,
                        '.',
                        lambda provider=provider: PROVIDERS[provider](
                            {'exe': sys.executable, 'reporter': 'tool'}, self.root, 2
                        ),
                    )
                    self.assertEqual(common.summary(rows)[1], 2)

    def test_npm_preserves_native_source_classification(self) -> None:
        _ = self.write('package.json', 'native-owned')
        _ = self.write('package-lock.json', 'native-owned')
        document = {
            'schemaVersion': 1,
            'results': [
                {'name': 'real-package', 'source': 'package-lock.json:node_modules/alias', 'state': 'unknown'},
                {'name': 'git', 'source': 'package-lock.json:node_modules/git', 'state': 'unknown'},
                {'name': 'local', 'source': 'package-lock.json:node_modules/local', 'state': 'skipped'},
            ],
        }
        with patch.object(npm, 'command_json', return_value=document):
            rows = list(npm.report({'exe': sys.executable, 'reporter': 'report'}, self.root, 2))
        self.assertEqual([r.state for r in rows], ['unknown', 'unknown', 'skipped'])
        self.assertEqual(rows[0].name, 'real-package')

    def test_enabled_missing_inputs_are_errors_for_every_provider(self) -> None:
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                rows = main.guarded(
                    provider,
                    provider,
                    '.',
                    lambda provider=provider: PROVIDERS[provider]({'exe': 'unreachable'}, self.root, 1),
                )
                self.assertEqual([r.state for r in rows], ['error'])

    def test_custom_adapter_and_cli_keep_partial_results(self) -> None:
        _ = self.write('flake.nix', '{}')
        _ = self.write('original', 'preserved')
        adapter = self.tool(
            'import pathlib,json\npathlib.Path("original").write_text("changed")\nprint(json.dumps({"schemaVersion":1,"results":[{"name":"custom","state":"outdated","current":"1","latest":"2"}]}))\n'
        )
        cfg: main.Config = {
            'treeRootFile': 'flake.nix',
            'timeout': 2,
            'concurrency': 2,
            'githubApi': 'https://api.github.com',
            'providers': {'uv': {'root': 'missing'}},
            'tools': [],
            'releases': [],
            'adapters': [{'name': 'custom', 'exe': adapter, 'args': [], 'root': '.'}],
        }
        config = self.js('config.json', cfg)
        result = subprocess.run(
            [
                sys.executable,
                str(pathlib.Path(main.__file__)),
                '--config',
                str(config),
                '--root',
                str(self.root),
                '--json',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        report = cast(dict[str, object], json.loads(result.stdout))
        self.assertEqual(report['counts'], {'error': 1, 'outdated': 1})
        self.assertEqual(self.root.joinpath('original').read_text(), 'preserved')
        cfg['providers'] = {}
        _ = config.write_text(json.dumps(cfg))
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(main.__file__)), '--config', str(config), '--no-color'],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('OUTDATED: OUTDATED', result.stdout)
        self.assertNotIn('Checking dependencies', result.stdout)

    def test_each_project_backend_preserves_original_on_failure(self) -> None:
        _ = self.write('Cargo.toml', '[workspace]\n')
        _ = self.write('Cargo.lock', 'version=4\n')
        _ = self.write('pyproject.toml', '[project]\n')
        _ = self.write('uv.lock', 'version=1\n')
        _ = self.js('package.json', {'packageManager': 'yarn@4.0.0'})
        _ = self.write('yarn.lock', '__metadata: {version: 8}\n')
        _ = self.write('pnpm-lock.yaml', 'importers: {}\n')
        _ = self.js(
            'package-lock.json',
            {
                'lockfileVersion': 3,
                'packages': {'node_modules/pkg': {'version': '1.0.0', 'resolved': 'https://registry/file.tgz'}},
            },
        )
        _ = self.js('composer.json', {})
        _ = self.js('composer.lock', {})
        tool = self.tool(
            'import pathlib,sys\nfor p in pathlib.Path.cwd().iterdir():\n if p.is_file(): p.write_text("mutated")\nsys.exit(3)\n'
        )
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        for provider in ('cargo', 'uv', 'npm', 'pnpm', 'yarn', 'composer'):
            with self.subTest(provider=provider):
                rows = main.guarded(
                    provider,
                    provider,
                    '.',
                    lambda provider=provider: PROVIDERS[provider](
                        {'exe': tool, 'reporter': tool, 'cargo': tool}, self.root, 2
                    ),
                )
                self.assertEqual([r.state for r in rows], ['error'])
                self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)


class MoreContracts(Fixture):
    def test_tyro_cli_help_and_option_errors(self) -> None:
        script = str(pathlib.Path(main.__file__))
        help_result = subprocess.run([sys.executable, script, '--help'], capture_output=True, text=True, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for flag in ('--config', '--root', '--json', '--no-color'):
            self.assertIn(flag, help_result.stdout)
        for arguments in ([], ['--unknown'], ['--config']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, script, *arguments], capture_output=True, text=True, check=False
                )
                self.assertEqual(result.returncode, 2)

    def test_main_runtime_contract_and_repeat_invocation(self) -> None:
        config = self.js(
            'report.json',
            {
                'treeRootFile': 'flake.nix',
                'timeout': 1,
                'concurrency': 2,
                'githubApi': 'https://api.github.com',
                'providers': {},
                'tools': [],
                'releases': [],
                'adapters': [],
            },
        )
        _ = self.write('flake.nix', '{}')
        argv = ['outdated', '--config', str(config), '--root', str(self.root), '--json']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.main(), 2)
        self.assertEqual(json.loads(output.getvalue())['state'], 'ERROR')
        client = network.Client()
        client.release = Mock(side_effect=[('v1.0', '1.0'), ('v2.0', '2.0')])
        document = cast(main.Config, json.loads(config.read_text()))
        document['releases'] = [{'name': 'fixture', 'version': '1.0', 'repo': 'o/r'}]
        _ = config.write_text(json.dumps(document))
        with patch.object(main, 'Client', return_value=client), patch.object(sys, 'argv', argv):
            for code in (0, 1):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main.main(), code)
        self.assertEqual(client.release.call_count, 2)
        _ = config.write_text('{')
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 2)

    def test_command_adapter_process_and_schema_errors(self) -> None:
        for body in ('print("not JSON")', 'print("{}")', 'import sys; sys.exit(1)'):
            exe = self.tool(body + '\n')
            config: main.Inventory = {
                'providers': {},
                'tools': [],
                'releases': [],
                'adapters': [{'name': 'failure', 'exe': exe, 'args': []}],
            }
            rows = main.guarded(*main.jobs(config, self.root, network.Client())[0])
            self.assertEqual(common.summary(rows)[1], 2)
        with self.assertRaises(common.Failure):
            _ = common.compare('p', 'x', '.', '', '')
        with self.assertRaises(common.Failure):
            _ = common.validate_adapter(
                {'schemaVersion': True, 'results': [{'name': 'x', 'state': 'skipped'}]}, 'p', '.'
            )

    def test_nix_empty_graph_missing_nodes_and_follows_shapes(self) -> None:
        _ = self.write('flake.lock', 'native-owned')
        _ = self.write('flake.nix', 'native-owned')
        with patch.object(
            sources, 'command_json', return_value={'locks': {'root': 'root', 'nodes': {'root': {}}}}
        ) as command:
            rows = [callback() for _, callback in sources.nix_jobs({'exe': 'nix'}, self.root, network.Client())]
        self.assertEqual(rows[0].state, 'up-to-date')
        self.assertIn('--no-update-lock-file', cast(list[str], command.call_args.args[0]))
        for edge in ('missing', 1, [1]):
            lock: sources.NixLock = {'root': 'root', 'nodes': {'root': {'inputs': {'bad': edge}}}}
            with self.assertRaises(common.Failure):
                _ = sources.input_owners(lock)
        lock = {'root': 'root', 'nodes': {'root': {'inputs': {'bad': 'bad'}}, 'bad': {}}}
        with patch.object(sources, 'command_json', return_value={'locks': lock}), self.assertRaises(common.Failure):
            _ = list(sources.nix_jobs({'exe': 'nix'}, self.root, network.Client()))

    def test_release_unknowns_pins_and_invalid_registries(self) -> None:
        client = network.Client()
        client.release = Mock()
        entries: tuple[tuple[sources.ReleaseEntry, str], ...] = (
            ({'name': 'fixture', 'unknown': 'custom'}, 'unknown'),
            ({'name': 'fixture', 'skip': 'intentional'}, 'skipped'),
        )
        for item, state in entries:
            row = sources.release_entry(client, item)
            self.assertEqual(row.state, state)
            client.release.assert_not_called()
        client.registry_release = Mock(side_effect=common.Failure('No releases'))
        with self.assertRaises(common.Failure):
            _ = sources.release_entry(client, {'name': 'fixture', 'provider': 'pypi', 'project': 'fixture'})
        with self.assertRaises(common.Failure):
            _ = sources.release_entry(client, {'name': 'fixture', 'provider': 'invalid'})
        real = network.Client()
        with patch.object(releases, 'lookup', return_value='v15'):
            self.assertEqual(real.release('o/r'), ('v15', 'v15'))

    def test_workflow_missing_malformed_and_no_external_actions(self) -> None:
        with self.assertRaises(common.Failure):
            _ = list(sources.workflow_jobs({}, self.root, network.Client()))
        path = self.write('.github/workflows/test.yml', 'jobs: {build: {steps: []}}\n')
        rows = [callback() for _, callback in sources.workflow_jobs({}, self.root, network.Client())]
        self.assertEqual(rows[0].state, 'up-to-date')
        for text in ('[]', 'jobs: []', 'jobs: {build: invalid}'):
            _ = path.write_text(text)
            with self.assertRaises(common.Failure):
                _ = list(sources.workflow_jobs({}, self.root, network.Client()))
        with self.assertRaises(common.Failure):
            _ = sources.action('unrecognised', '.github/workflows/test.yml', network.Client())

    def test_native_unknown_graph_and_dev_branches(self) -> None:
        _ = self.write('Cargo.toml', 'native-owned')
        _ = self.write('Cargo.lock', 'native-owned')
        inventory = {'packages': [{'name': 'git', 'version': '1.0', 'source': 'git+https://example.test/repo'}]}
        document = {'dependencies': [{'name': 'changed', 'project': '---', 'compat': '---', 'latest': 'Removed'}]}
        with patch.object(cargo, 'command_json', side_effect=[inventory, document]):
            rows = list(cargo.report({'exe': 'tool', 'cargo': 'cargo'}, self.root, 1))
        self.assertEqual([r.state for r in rows], ['unknown', 'unknown'])
        with patch.object(cargo, 'command_json', side_effect=[inventory, {'dependencies': []}]):
            rows = list(cargo.report({'exe': 'tool', 'cargo': 'cargo'}, self.root, 1))
        self.assertEqual(common.summary(rows)[1], 2)
        _ = self.write('composer.json', 'native-owned')
        _ = self.write('composer.lock', 'native-owned')
        with patch.object(
            composer, 'command_json', return_value={'locked': [{'name': 'branch', 'version': 'dev-main'}]}
        ):
            rows = list(composer.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        self.assertEqual(rows[0].state, 'unknown')

    def test_native_yarn_invalid_report_and_failed_plugin_setup(self) -> None:
        _ = self.write('package.json', 'native-owned')
        _ = self.write('yarn.lock', 'native-owned')
        documents: tuple[object, ...] = (
            {},
            {'schemaVersion': 1, 'results': []},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'invalid'}]},
        )
        for document in documents:
            with (
                patch.object(yarn, 'run'),
                patch.object(yarn, 'command_json', return_value=document),
                self.assertRaises(common.Failure),
            ):
                _ = list(yarn.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        with (
            patch.object(yarn, 'run', side_effect=common.Failure('setup failed')),
            patch.object(yarn, 'command_json') as report,
            self.assertRaises(common.Failure),
        ):
            _ = list(yarn.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        report.assert_not_called()


if __name__ == '__main__':
    _ = unittest.main()
