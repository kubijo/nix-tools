"""Hermetic contracts for dependency inventories, failure reporting and source preservation."""

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from email.message import Message
from typing import cast
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, sys.argv.pop(1))
import common
import main
import network
import releases
import sources
from providers import PROVIDERS, cargo, composer, npm, pnpm, uv, yarn


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def js(self, name, data):
        return self.write(name, json.dumps(data))

    def tool(self, body):
        path = self.write('tool', f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
        return str(path)


class Protocol(Fixture):
    def test_registered_providers_dispatch_with_public_names(self):
        names = {'cargo', 'composer', 'npm', 'pnpm', 'uv', 'yarn'}
        self.assertEqual(set(PROVIDERS), names)
        settings = {name: {'exe': name} for name in names}
        reports = {name: Mock(return_value=[common.Result(name, 'fixture', '.', 'up-to-date')]) for name in names}
        config = {'providers': settings, 'tools': [], 'releases': [], 'adapters': []}
        client = network.Client(timeout=7)
        with patch.dict(PROVIDERS, reports):
            jobs = main.jobs(config, self.root, client)
            self.assertEqual({job[0] for job in jobs}, names)
            for job in jobs:
                rows = main.guarded(*job)
                self.assertEqual([(row.provider, row.state) for row in rows], [(job[0], 'up-to-date')])
                reports[job[0]].assert_called_once_with(settings[job[0]], self.root, 7)

    def test_runtime_version_discovery_reuses_release_lookup_and_preserves_source(self):
        original = self.write('source', 'unchanged')
        command = self.tool('import pathlib\npathlib.Path("source").write_text("mutated")\nprint("tool 1.2.3")\n')
        client = network.Client()
        client.release = Mock(return_value=('v2.0.0', '2.0.0'))
        item = {
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
                sources.release_entry(client, item, root=self.root)
        for error in (common.Failure('Provider command timed out'), common.Failure('Provider command failed')):
            with patch.object(sources, 'run', side_effect=error), self.assertRaises(common.Failure):
                sources.release_entry(client, item, root=self.root)

    def test_static_skips_do_not_copy_source_or_execute_commands(self):
        config = {
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

    def test_exit_precedence_and_empty_reports(self):
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

    def test_versions(self):
        for before, after, expected in [
            ('3.9', '3.14', 'outdated'),
            ('v1.2.0', '1.2', 'up-to-date'),
            ('php-8.5.1', '8.5.0', 'unknown'),
            ('0.5.8-unstable-2026-07-17', '0.5.8', 'ahead'),
            ('0.5.8-unstable-2026-07-17', '0.5.9', 'outdated'),
            ('snapshot', 'v1', 'unknown'),
        ]:
            self.assertEqual(common.compare('test', 'x', '.', before, after).state, expected)

    def test_adapter_protocol(self):
        good = {'schemaVersion': 1, 'results': [{'name': 'thing', 'state': 'outdated', 'current': '1', 'latest': '2'}]}
        self.assertEqual(common.validate_adapter(good, 'custom', '.')[0].latest, '2')
        for bad in [
            None,
            [],
            {},
            {'schemaVersion': 2, 'results': []},
            {'schemaVersion': 1, 'results': []},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'success'}]},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'up-to-date', 'secret': 'x'}]},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'up-to-date', 'current': 1}]},
        ]:
            with self.subTest(bad=bad), self.assertRaises(common.Failure):
                common.validate_adapter(bad, 'custom', '.')

    def test_partial_results_and_sensitive_exceptions(self):
        def partial():
            yield common.Result('p', 'first', '.', 'outdated')
            raise ValueError('secret-token')

        rows = main.guarded('p', 'next', '.', partial)
        self.assertEqual([row.state for row in rows], ['outdated', 'error'])
        self.assertNotIn('secret-token', rows[-1].detail)
        self.assertEqual(main.guarded('p', 'x', '.', list)[0].state, 'error')
        self.assertEqual(main.guarded('p', 'x', '.', lambda: [1])[0].state, 'error')

    def test_redaction_and_json_output(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'sensitive-token'}):
            value = main.clean('\x1b[31mhttps://user:pass@example.test/?token=abc sensitive-token')
        self.assertNotIn('user:pass', value)
        self.assertNotIn('sensitive-token', value)
        self.assertNotIn('abc', value)
        self.assertNotIn('\x1b', value)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main.report([common.Result('x', 'name', '.', 'outdated', '1', '1.1', '2')], True)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['schemaVersion'], 1)

    def test_unknown_is_gray_only_on_interactive_text_output(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

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
                main.report([row], False)
            self.assertNotIn('\x1b[90m', output.getvalue())

    def test_snapshot_preserves_files_and_rejects_links(self):
        original = self.write('manifest', 'original')
        original.chmod(0o751)
        self.write('.git/index', 'index')
        self.write('.venv/large-file', 'environment')
        with common.snapshot(self.root) as work:
            self.assertFalse((work / '.git').exists())
            self.assertFalse((work / '.venv').exists())
            (work / 'manifest').write_text('changed')
        self.assertEqual(original.read_text(), 'original')
        self.assertEqual(original.stat().st_mode & 0o777, 0o751)
        (self.root / 'outside').symlink_to('/etc')
        with self.assertRaises(common.Failure), common.snapshot(self.root):
            pass
        (self.root / 'outside').unlink()
        (self.root / 'loop').symlink_to(self.root)
        with self.assertRaises(common.Failure), common.snapshot(self.root):
            pass

    def test_command_errors_and_timeout(self):
        bad = self.tool('import sys\nprint("private-credential", file=sys.stderr)\nsys.exit(3)\n')
        with self.assertRaises(common.Failure) as failure:
            common.run([bad], self.root, 2)
        self.assertNotIn('private-credential', str(failure.exception))
        sleepy = self.tool('import time\ntime.sleep(10)\n')
        with self.assertRaisesRegex(common.Failure, 'timed out'):
            common.run([sleepy], self.root, 0.05)
        bad = self.tool('print("not json")\n')
        with self.assertRaisesRegex(common.Failure, 'malformed'):
            common.command_json([bad], self.root, 2)

    def test_root_and_missing_provider_failures(self):
        self.write('flake.nix', '{}')
        nested = self.root / 'nested'
        nested.mkdir()
        self.assertEqual(main.find_root(nested, 'flake.nix'), self.root)
        with self.assertRaises(common.Failure):
            common.relative(self.root, '../outside')
        config = {'providers': {'uv': {'root': 'missing'}}, 'tools': [], 'releases': [], 'adapters': []}
        work = main.jobs(config, self.root, network.Client())
        self.assertEqual(main.guarded(*work[0])[0].state, 'error')


class Releases(Fixture):
    def test_unreadable_versions_skip_without_package_exceptions(self):
        client = network.Client()
        for latest in ('php-8.5.1', 'cli/v2.0', 'snapshot', '3.0rc1'):
            with patch.object(releases, 'lookup', return_value=latest):
                rows = main.guarded('tools', 'fixture', '.', lambda: client.release('owner/repo'))
            self.assertEqual(rows[0].state, 'skipped')
            self.assertEqual(common.summary(rows)[1], 0)
            with patch.object(releases, 'lookup', return_value=latest):
                row = sources.release_entry(client, {'name': 'fixture', 'repo': 'owner/repo', 'version': '1.0'})
            self.assertEqual((row.state, row.current), ('skipped', '1.0'))

    def test_nvchecker_events_errors_and_no_result(self):
        for event in ('updated', 'up-to-date'):
            data = json.dumps({'event': event, 'name': 'entry', 'version': '2.0'})
            with patch.object(releases, 'run', return_value=(data, '')):
                self.assertEqual(releases.lookup('nvchecker', {}, 2), '2.0')
        no_result = {'event': 'no-result', 'level': 'error', 'name': 'entry'}
        with (
            patch.object(releases, 'run', return_value=(json.dumps(no_result), '')),
            self.assertRaises(common.UnreadableSource),
        ):
            releases.lookup('nvchecker', {}, 2)
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
            releases.lookup('nvchecker', {}, 2)

    def test_runtime_config_is_private_temporary_and_not_in_argv(self):
        import tomllib

        paths = []

        def execute(argv, root, timeout, **kwargs):
            path = pathlib.Path(argv[2])
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            config = tomllib.loads(path.read_text())
            self.assertEqual(config['entry']['token'], 'private-secret')
            self.assertFalse('oldver' in config['__config__'] or 'newver' in config['__config__'])
            self.assertNotIn('private-secret', str(argv))
            return json.dumps({'event': 'updated', 'name': 'entry', 'version': 'v2.0'}), ''

        with patch.dict(os.environ, {'GH_TOKEN': 'private-secret'}), patch.object(releases, 'run', side_effect=execute):
            self.assertEqual(network.Client().release('owner/repo'), ('v2.0', 'v2.0'))
        self.assertFalse(paths[0].exists())

    def test_network_failure_is_not_a_tag_fallback(self):
        client = network.Client()
        with (
            patch.object(releases, 'lookup', side_effect=common.Failure('Lookup failed')) as lookup,
            self.assertRaises(common.Failure),
        ):
            client.release('owner/repo')
        self.assertEqual(lookup.call_count, 1)
        with self.assertRaises(common.Failure):
            client.release('https://secret@evil/repo')
        with patch.object(releases, 'lookup') as lookup, self.assertRaises(common.UnreadableSource):
            network.Client(github='https://enterprise.example/api').release('owner/repo')
        lookup.assert_not_called()

    def test_registry_release_dispatch(self):
        client = Mock()
        client.registry_release.return_value = '1.0'
        row = sources.release_entry(client, {'name': 'thing', 'provider': 'pypi', 'project': 'thing', 'version': '1.0'})
        self.assertEqual(row.state, 'up-to-date')

    def test_nix_follows_and_owners(self):
        root_inputs: dict[str, str | list[str]] = {'tools': 'tools', 'pkgs': 'pkgs'}
        lock = {
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
            sources.input_owners(lock)

    def test_nix_pins_branches_and_releases(self):
        client = Mock()
        client.release.return_value = ('v2.0', '2.0')
        client.commit.return_value = 'b' * 40
        node = {'locked': {'type': 'github', 'owner': 'o', 'repo': 'r', 'rev': 'a' * 40}, 'original': {'ref': 'v1.0'}}
        row = sources.nix_input(node, 'tools/dependency', {}, self.root, client)
        self.assertEqual(row.state, 'outdated')
        self.assertIn('owning top-level input: tools', row.detail)
        client.release.assert_called_once_with('o/r', tags=True)
        client.commit.assert_called_with('o/r', 'v2.0')
        client.release.side_effect = common.UnreadableSource('No readable release')
        skipped = sources.nix_input(node, 'tools', {}, self.root, client)
        self.assertEqual((skipped.state, skipped.current), ('skipped', 'a' * 40))
        skipped = sources.action('o/r@v1.0', 'workflow.yml', client)
        self.assertEqual((skipped.state, skipped.current), ('skipped', 'v1.0'))
        node['original'] = {'rev': 'a' * 40}
        self.assertEqual(sources.nix_input(node, 'tools', {}, self.root, client).state, 'pinned')
        node['locked']['type'] = 'path'
        node['original'] = {}
        self.assertEqual(sources.nix_input(node, 'tools', {}, self.root, client).state, 'unknown')

    def test_workflows_subpaths_reusable_and_local(self):
        self.write(
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
        client = Mock()
        client.release.return_value = ('v1.2.0', '1.2.0')
        client.commit.return_value = 'a' * 40
        work = list(sources.workflow_jobs({}, self.root, client))
        rows = [callback() for _, callback in work]
        self.assertEqual(Counter(row.state for row in rows), {'unknown': 2, 'skipped': 1, 'up-to-date': 2})


class Native(Fixture):
    def test_uv_lock_uses_declared_registry_and_marks_other_sources(self):
        self.write('pyproject.toml', '[project]\nname="test"\n')
        self.write(
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
        response = {
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
        self.assertIn('--locked', command.call_args.args[0])
        response['resolution']['p'].pop('latest_version')
        with patch.object(uv, 'command_json', return_value=response):
            rows = list(uv.report({'exe': 'uv'}, self.root, 2))
            self.assertEqual((rows[0].state, rows[0].current), ('unknown', '1.0'))
            self.assertEqual(common.summary(rows)[1], 2)

    def test_cargo_workspace_compatibility(self):
        self.write('Cargo.toml', 'native-owned')
        self.write('Cargo.lock', 'native-owned')
        document = {'dependencies': [{'name': 'pkg', 'project': '1.0.0', 'compat': '1.1.0', 'latest': '2.0.0'}]}
        with patch.object(cargo, 'command_json', side_effect=[{'packages': []}, document]) as command:
            rows = list(cargo.report({'exe': 'cargo-outdated', 'cargo': 'cargo'}, self.root, 2))
        self.assertEqual(rows[0].compatible, '1.1.0')
        self.assertIn('--locked', command.call_args_list[0].args[0])
        self.assertNotEqual(command.call_args.args[1], self.root)

    def test_composer_locked_and_pnpm_workspaces(self):
        self.write('composer.json', 'native-owned')
        self.write('composer.lock', 'native-owned')
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
            self.assertIn(flag, command.call_args.args[0])
        self.write('package.json', 'native-owned')
        self.write('pnpm-lock.yaml', 'native-owned')

        def report(args, directory, timeout, **kwargs):
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

    def test_npm_inventory_comes_from_lock_not_install(self):
        self.write('package.json', 'native-owned')
        self.write('package-lock.json', 'native-owned')
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

    def test_yarn_consumes_native_report_without_reading_lock(self):
        self.write('package.json', 'native-owned')
        self.write('yarn.lock', 'native-owned')
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

    def test_native_failure_cannot_mutate_source(self):
        self.js('package.json', {})
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
            list(npm.report({'exe': tool, 'reporter': tool}, self.root, 2))
        self.assertEqual(lock.read_bytes(), original)


class EdgeCases(Fixture):
    def test_all_unknown_protocols_remain_nonzero(self):
        for state in ('unknown', 'blocked', 'error'):
            with self.subTest(state=state), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.report([common.Result('p', 'x', '.', state)], True), 2)

    def test_invalid_result_fields_do_not_crash_report(self):
        # Deliberately violate the annotation to exercise runtime protocol validation.
        for row in (common.Result('p', 'x', '.', 'invalid'), common.Result('p', 'x', '.', 'skipped', cast(str, None))):
            self.assertEqual(main.guarded('p', 'x', '.', lambda row=row: row)[0].state, 'error')

    def test_redirects_never_forward_credentials_across_origins(self):
        import urllib.request

        handler = network.SafeRedirect()
        request = urllib.request.Request('https://api.example/a', headers={'Authorization': 'Bearer secret'})
        for target in ('http://api.example/b', 'https://other.example/b', 'https://api.example:123/b'):
            with self.subTest(target=target), self.assertRaises(common.Failure):
                handler.redirect_request(request, None, 302, 'redirect', {}, target)
        new = handler.redirect_request(request, None, 302, 'redirect', {}, 'https://api.example/b')
        self.assertEqual(new.get_header('Authorization'), 'Bearer secret')

    def test_http_auth_and_failure_redaction(self):
        import urllib.error

        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = io.StringIO('{"ok": true}')
        with (
            patch.dict(os.environ, {'GH_TOKEN': 'secret-value'}),
            patch.object(network.urllib.request, 'build_opener', return_value=opener),
        ):
            client = network.Client()
            self.assertEqual(client.api('test'), {'ok': True})
            self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer secret-value')
            opener.open.return_value.__enter__.return_value = io.StringIO('{}')
            client.get('https://pypi.org/test')
            self.assertIsNone(opener.open.call_args.args[0].get_header('Authorization'))
            opener.open.side_effect = urllib.error.HTTPError('https://secret@example', 429, 'private', Message(), None)
            with self.assertRaisesRegex(common.Failure, 'HTTP 429') as caught:
                client.api('limited')
            self.assertNotIn('secret', str(caught.exception))

    def test_release_policy_custom_patterns_and_empty(self):
        client = network.Client()
        with patch.object(releases, 'lookup', return_value='cli/v2.0'):
            self.assertEqual(
                client.release('o/r', tags=True, tagPattern=r'cli/v(?P<version>\d+\.\d+)'), ('cli/v2.0', '2.0')
            )
        client.api = Mock(return_value={'sha': 'not-a-commit'})
        with self.assertRaises(common.Failure):
            client.commit('o/r', 'a/b')

    def test_git_tags_and_annotated_nix_tags(self):
        client = Mock(timeout=2)
        client.registry_release.return_value = '2.0'
        row = sources.release_entry(
            client,
            {'name': 'git', 'version': '1.0', 'provider': 'git', 'url': 'https://example.test/r'},
        )
        self.assertEqual(row.latest, '2.0')
        node = {'locked': {'type': 'git', 'rev': 'b', 'url': 'https://example.test/r'}, 'original': {'ref': 'v1'}}
        with patch.object(sources, 'run', return_value=('a\trefs/tags/v1\nb\trefs/tags/v1^{}\n', '')):
            self.assertEqual(sources.nix_input(node, 'n', {'git': 'git'}, self.root, client).state, 'up-to-date')
        with patch.object(sources, 'run', return_value=('', '')), self.assertRaises(common.Failure):
            sources.nix_input(node, 'n', {'git': 'git'}, self.root, client)

    def test_native_empty_and_invalid_schemas(self):
        for name in ('package.json', 'composer.json', 'composer.lock', 'package-lock.json', 'pnpm-lock.yaml'):
            self.write(name, 'native-owned')
        with patch.object(composer, 'command_json', return_value={'locked': []}):
            self.assertEqual(
                next(composer.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 2)).state, 'up-to-date'
            )
        with patch.object(npm, 'command_json', return_value={'schemaVersion': 1, 'results': []}):
            self.assertEqual(
                next(npm.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 2)).state, 'up-to-date'
            )
        for provider in ('npm', 'composer', 'pnpm'):
            for document in ({}, [], {'schemaVersion': 1, 'results': [{'state': 'up-to-date'}]}):
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

    def test_npm_preserves_native_source_classification(self):
        self.write('package.json', 'native-owned')
        self.write('package-lock.json', 'native-owned')
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

    def test_enabled_missing_inputs_are_errors_for_every_provider(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                rows = main.guarded(
                    provider,
                    provider,
                    '.',
                    lambda provider=provider: PROVIDERS[provider]({'exe': 'unreachable'}, self.root, 1),
                )
                self.assertEqual([r.state for r in rows], ['error'])

    def test_custom_adapter_and_cli_keep_partial_results(self):
        self.write('flake.nix', '{}')
        self.write('original', 'preserved')
        adapter = self.tool(
            'import pathlib,json\npathlib.Path("original").write_text("changed")\nprint(json.dumps({"schemaVersion":1,"results":[{"name":"custom","state":"outdated","current":"1","latest":"2"}]}))\n'
        )
        cfg = {
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
        report = json.loads(result.stdout)
        self.assertEqual(report['counts'], {'error': 1, 'outdated': 1})
        self.assertEqual(self.root.joinpath('original').read_text(), 'preserved')
        cfg['providers'] = {}
        config.write_text(json.dumps(cfg))
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(main.__file__)), '--config', str(config), '--no-color'],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('OUTDATED: OUTDATED', result.stdout)

    def test_each_project_backend_preserves_original_on_failure(self):
        self.write('Cargo.toml', '[workspace]\n')
        self.write('Cargo.lock', 'version=4\n')
        self.write('pyproject.toml', '[project]\n')
        self.write('uv.lock', 'version=1\n')
        self.js('package.json', {'packageManager': 'yarn@4.0.0'})
        self.write('yarn.lock', '__metadata: {version: 8}\n')
        self.write('pnpm-lock.yaml', 'importers: {}\n')
        self.js(
            'package-lock.json',
            {
                'lockfileVersion': 3,
                'packages': {'node_modules/pkg': {'version': '1.0.0', 'resolved': 'https://registry/file.tgz'}},
            },
        )
        self.js('composer.json', {})
        self.js('composer.lock', {})
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
    def test_tyro_cli_help_and_option_errors(self):
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

    def test_main_runtime_contract_and_repeat_invocation(self):
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
        self.write('flake.nix', '{}')
        argv = ['outdated', '--config', str(config), '--root', str(self.root), '--json']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main.main(), 2)
        self.assertEqual(json.loads(output.getvalue())['state'], 'ERROR')
        client = Mock()
        client.release.side_effect = [('v1.0', '1.0'), ('v2.0', '2.0')]
        document = json.loads(config.read_text())
        document['releases'] = [{'name': 'fixture', 'version': '1.0', 'repo': 'o/r'}]
        config.write_text(json.dumps(document))
        with patch.object(main, 'Client', return_value=client), patch.object(sys, 'argv', argv):
            for code in (0, 1):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main.main(), code)
        self.assertEqual(client.release.call_count, 2)
        config.write_text('{')
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 2)

    def test_command_adapter_process_and_schema_errors(self):
        for body in ('print("not JSON")', 'print("{}")', 'import sys; sys.exit(1)'):
            exe = self.tool(body + '\n')
            config = {
                'providers': {},
                'tools': [],
                'releases': [],
                'adapters': [{'name': 'failure', 'exe': exe, 'args': []}],
            }
            rows = main.guarded(*main.jobs(config, self.root, network.Client())[0])
            self.assertEqual(common.summary(rows)[1], 2)
        with self.assertRaises(common.Failure):
            common.compare('p', 'x', '.', '', '')
        with self.assertRaises(common.Failure):
            common.validate_adapter({'schemaVersion': True, 'results': [{'name': 'x', 'state': 'skipped'}]}, 'p', '.')

    def test_nix_empty_graph_missing_nodes_and_follows_shapes(self):
        self.write('flake.lock', 'native-owned')
        self.write('flake.nix', 'native-owned')
        with patch.object(
            sources, 'command_json', return_value={'locks': {'root': 'root', 'nodes': {'root': {}}}}
        ) as command:
            rows = [callback() for _, callback in sources.nix_jobs({'exe': 'nix'}, self.root, network.Client())]
        self.assertEqual(rows[0].state, 'up-to-date')
        self.assertIn('--no-update-lock-file', command.call_args.args[0])
        for edge in ('missing', 1, [1]):
            lock = {'root': 'root', 'nodes': {'root': {'inputs': {'bad': edge}}}}
            with self.assertRaises(common.Failure):
                sources.input_owners(lock)
        lock = {'root': 'root', 'nodes': {'root': {'inputs': {'bad': 'bad'}}, 'bad': {}}}
        with patch.object(sources, 'command_json', return_value={'locks': lock}), self.assertRaises(common.Failure):
            list(sources.nix_jobs({'exe': 'nix'}, self.root, network.Client()))

    def test_release_unknowns_pins_and_invalid_registries(self):
        client = Mock()
        for item, state in (({'unknown': 'custom'}, 'unknown'), ({'skip': 'intentional'}, 'skipped')):
            row = sources.release_entry(client, {'name': 'fixture'} | item)
            self.assertEqual(row.state, state)
            client.release.assert_not_called()
        client.get.return_value = {'releases': {}}
        with self.assertRaises(common.Failure):
            sources.release_entry(client, {'name': 'fixture', 'provider': 'pypi', 'project': 'fixture'})
        with self.assertRaises(common.Failure):
            sources.release_entry(client, {'name': 'fixture', 'provider': 'invalid'})
        real = network.Client()
        with patch.object(releases, 'lookup', return_value='v15'):
            self.assertEqual(real.release('o/r'), ('v15', 'v15'))

    def test_workflow_missing_malformed_and_no_external_actions(self):
        with self.assertRaises(common.Failure):
            list(sources.workflow_jobs({}, self.root, network.Client()))
        path = self.write('.github/workflows/test.yml', 'jobs: {build: {steps: []}}\n')
        rows = [callback() for _, callback in sources.workflow_jobs({}, self.root, network.Client())]
        self.assertEqual(rows[0].state, 'up-to-date')
        for text in ('[]', 'jobs: []', 'jobs: {build: invalid}'):
            path.write_text(text)
            with self.assertRaises(common.Failure):
                list(sources.workflow_jobs({}, self.root, network.Client()))
        with self.assertRaises(common.Failure):
            sources.action('unrecognised', '.github/workflows/test.yml', network.Client())

    def test_native_unknown_graph_and_dev_branches(self):
        self.write('Cargo.toml', 'native-owned')
        self.write('Cargo.lock', 'native-owned')
        inventory = {'packages': [{'name': 'git', 'version': '1.0', 'source': 'git+https://example.test/repo'}]}
        document = {'dependencies': [{'name': 'changed', 'project': '---', 'compat': '---', 'latest': 'Removed'}]}
        with patch.object(cargo, 'command_json', side_effect=[inventory, document]):
            rows = list(cargo.report({'exe': 'tool', 'cargo': 'cargo'}, self.root, 1))
        self.assertEqual([r.state for r in rows], ['unknown', 'unknown'])
        with patch.object(cargo, 'command_json', side_effect=[inventory, {'dependencies': []}]):
            rows = list(cargo.report({'exe': 'tool', 'cargo': 'cargo'}, self.root, 1))
        self.assertEqual(common.summary(rows)[1], 2)
        self.write('composer.json', 'native-owned')
        self.write('composer.lock', 'native-owned')
        with patch.object(
            composer, 'command_json', return_value={'locked': [{'name': 'branch', 'version': 'dev-main'}]}
        ):
            rows = list(composer.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        self.assertEqual(rows[0].state, 'unknown')

    def test_native_yarn_invalid_report_and_failed_plugin_setup(self):
        self.write('package.json', 'native-owned')
        self.write('yarn.lock', 'native-owned')
        for document in (
            {},
            {'schemaVersion': 1, 'results': []},
            {'schemaVersion': 1, 'results': [{'name': 'x', 'state': 'invalid'}]},
        ):
            with (
                patch.object(yarn, 'run'),
                patch.object(yarn, 'command_json', return_value=document),
                self.assertRaises(common.Failure),
            ):
                list(yarn.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        with (
            patch.object(yarn, 'run', side_effect=common.Failure('setup failed')),
            patch.object(yarn, 'command_json') as report,
            self.assertRaises(common.Failure),
        ):
            list(yarn.report({'exe': sys.executable, 'reporter': 'tool'}, self.root, 1))
        report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
