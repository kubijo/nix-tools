"""Real pinned clients against a loopback registry; no internet or installation."""

import base64
import gzip
import hashlib
import http.server
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import Mock, patch

settings = json.loads(pathlib.Path(sys.argv.pop(1)).read_text())
fixtures = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, sys.argv.pop(1))
import common
import main
import network
import sources
import yaml
from providers import PROVIDERS


class Registry(http.server.BaseHTTPRequestHandler):
    denied_status = 401
    release_status = 200
    release_tag = 'v2.0'
    release_tags = ('v1.0', 'v2.0', 'v3.0rc1', 'unrelated/v99.0')

    def do_HEAD(self):
        if not self.path.endswith('.whl'):
            self.send_error(404)
            return
        name, version = self.path.strip('/').removesuffix('-py3-none-any.whl').rsplit('-', 1)
        self.send_response(200)
        self.send_header('Content-Length', str(len(python_wheel(name, version))))
        self.end_headers()

    def do_GET(self):
        if self.path.startswith(('/repos/', '/pypi/', '/api/v1/crates/')) and self.release_status != 200:
            self.send_error(self.release_status)
            return
        if self.path.startswith('/private/'):
            expected = 'Basic ' + base64.b64encode(b'fixture:fixture-password').decode()
            if self.headers.get('Authorization') != expected:
                self.send_response(self.denied_status)
                self.send_header('WWW-Authenticate', 'Basic realm="fixture"')
                self.end_headers()
                return
        assert isinstance(self.server, http.server.HTTPServer)
        base = f'http://localhost:{self.server.server_port}'
        if self.path.startswith('/is-number-') and self.path.endswith('.tgz'):
            version = self.path.removeprefix('/is-number-').removesuffix('.tgz')
            self.send_response(200)
            self.end_headers()
            self.wfile.write(npm_archive(version))
            return
        if self.path.endswith('.whl'):
            name, version = self.path.strip('/').removesuffix('-py3-none-any.whl').rsplit('-', 1)
            wheel = python_wheel(name, version)
            self.send_response(200)
            self.send_header('Content-Length', str(len(wheel)))
            self.end_headers()
            self.wfile.write(wheel)
            return
        if self.path.startswith('/crates/'):
            version = self.path.split('/')[3]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(crate(version))
            return
        if self.path.startswith('/index/'):
            self.send_response(200)
            self.end_headers()
            if self.path.endswith('config.json'):
                value = {'dl': base + '/crates/{crate}/{version}/download'}
                self.wfile.write(json.dumps(value).encode())
            else:
                for version in ('1.0.20', '1.0.21'):
                    self.wfile.write(
                        (
                            json.dumps(
                                {
                                    'name': 'semver',
                                    'vers': version,
                                    'deps': [],
                                    'features': {},
                                    'yanked': False,
                                    'cksum': hashlib.sha256(crate(version)).hexdigest(),
                                }
                            )
                            + '\n'
                        ).encode()
                    )
            return
        if self.path.startswith('/api/v1/crates/'):
            value = {
                'versions': [
                    {'num': '0.24.1', 'yanked': False},
                    {'num': '0.24.2', 'yanked': False},
                    {'num': '0.25.0', 'yanked': True},
                    {'num': '0.26.0-rc.1', 'yanked': False},
                ]
            }
            content = 'application/json'
        elif self.path.startswith('/pypi/'):
            value = {'releases': {'1.0': [{'yanked': False}], '2.0': [{'yanked': True}], '3.0rc1': [{'yanked': False}]}}
            content = 'application/json'
        elif self.path.startswith('/repos/'):
            if self.path.endswith('/git/refs/tags'):
                value = [{'ref': 'refs/tags/' + tag, 'object': {'sha': 'a' * 40}} for tag in self.release_tags]
            else:
                value = {'tag_name': self.release_tag, 'html_url': 'https://example.test/release'}
            content = 'application/json'
        elif self.path.startswith('/composer'):
            value = {
                'packages': {
                    'psr/log': {
                        version: {
                            'name': 'psr/log',
                            'version': version,
                            'type': 'library',
                            'dist': {'url': base + '/unused.zip', 'type': 'zip'},
                        }
                        for version in ('1.1.4', '3.0.2')
                    }
                }
            }
            content = 'application/json'
        elif '/simple/' in self.path:
            package = self.path.strip('/').split('/')[-1]
            value = {
                'meta': {'api-version': '1.0'},
                'name': package,
                'files': [
                    {
                        'filename': f'{package.replace("-", "_")}-{v}-py3-none-any.whl',
                        'url': f'{base}/{package.replace("-", "_")}-{v}-py3-none-any.whl',
                        'hashes': {'sha256': hashlib.sha256(python_wheel(package, v)).hexdigest()},
                        'requires-python': '>=3.8',
                        'yanked': False,
                    }
                    for v in ('24.0', '25.0')
                ],
            }
            content = 'application/vnd.pypi.simple.v1+json'
        else:
            value = {
                'name': getattr(self, 'npm_name', 'is-number'),
                'dist-tags': {'latest': getattr(self, 'npm_versions', ('7.0.0',))[-1]},
                'versions': {
                    v: {
                        'name': getattr(self, 'npm_name', 'is-number'),
                        'version': v,
                        'dist': {
                            'tarball': f'{base}/is-number-{v}.tgz',
                            'shasum': hashlib.sha1(npm_archive(v)).hexdigest(),
                        },
                    }
                    for v in getattr(self, 'npm_versions', ('5.0.0', '6.0.0', '7.0.0'))
                },
            }
            content = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', content)
        self.end_headers()
        self.wfile.write(json.dumps(value, separators=(',', ':')).encode())

    def log_message(self, format, *args):
        pass


def crate(version):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        files = {'Cargo.toml': f'[package]\nname="semver"\nversion="{version}"\nedition="2021"\n', 'src/lib.rs': ''}
        for path, text in files.items():
            info = tarfile.TarInfo(f'semver-{version}/{path}')
            info.size = len(text.encode())
            archive.addfile(info, io.BytesIO(text.encode()))
    return gzip.compress(buffer.getvalue(), mtime=0)


def npm_archive(version):
    buffer = io.BytesIO()
    data = json.dumps({'name': 'is-number', 'version': version}).encode()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        info = tarfile.TarInfo('package/package.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


def python_wheel(name, version):
    buffer = io.BytesIO()
    prefix = f'{name.replace("-", "_")}-{version}.dist-info/'
    with zipfile.ZipFile(buffer, 'w') as archive:
        for file, content in {
            'METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
            'WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
            'RECORD': '',
        }.items():
            archive.writestr(zipfile.ZipInfo(prefix + file), content)
    return buffer.getvalue()


def digest(root):
    return {
        str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode)
        for p in root.rglob('*')
        if p.is_file()
    }


class NativeClients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('localhost', 0), Registry)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.registry = f'http://localhost:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        environment = patch.dict(os.environ, {'OUTDATED_TEST_REGISTRY': self.registry})
        environment.start()
        self.addCleanup(environment.stop)

    def release_client(self):
        return network.Client(timeout=10, nvchecker=settings['nvchecker'])

    def js(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def generate(self, command):
        result = subprocess.run(
            command, cwd=self.root, env=common.environment(), capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def invoke(self, name):
        before = digest(self.root)
        try:
            rows = list(
                PROVIDERS[name](
                    {
                        'exe': settings[name],
                        'reporter': settings['npmReporter'],
                        'semver': settings['semver'],
                        'cargo': settings['cargoMetadata'],
                    },
                    self.root,
                    30,
                )
            )
        finally:
            self.assertEqual(before, digest(self.root))
        return rows

    def project(self, manager=None):
        self.js(
            'package.json',
            {
                'name': 'public-provider-fixture',
                'version': '0.0.0',
                'private': True,
                'dependencies': {'is-number': '6.0.0'},
            }
            | ({'packageManager': manager} if manager else {}),
        )
        (self.root / '.npmrc').write_text(f'registry={self.registry}\n')

    def test_explicit_registry_entries_use_native_sources(self):
        catalog = {row['name']: row for row in settings['releaseExamples']}
        self.assertEqual(catalog['biome']['provider'], 'npm')
        self.assertEqual(catalog['biome']['project'], '@biomejs/biome')
        self.assertEqual(catalog['lychee']['provider'], 'crates')
        self.assertEqual(catalog['lychee']['project'], 'lychee')
        self.assertTrue(all('tagPattern' not in row for row in catalog.values()))
        # Captured adoption releases include unrelated monorepo namespaces.
        captured = json.loads((fixtures / 'releases.json').read_text())
        self.assertIn('@biomejs/biome@2.5.15', [r['tag_name'] for r in captured['biomejs-biome']])
        self.assertIn('lychee-lib-v0.24.2', [r['tag_name'] for r in captured['lycheeverse-lychee']])
        client = self.release_client()
        client.release = Mock(side_effect=AssertionError('No GitHub tag lookup'))
        # Explicit examples exercise the real nvchecker registry plugins.
        with (
            patch.object(Registry, 'npm_name', '@biomejs/biome', create=True),
            patch.object(Registry, 'npm_versions', ('2.5.14', '2.5.15'), create=True),
        ):
            row = sources.release_entry(client, catalog['biome'] | {'version': '2.5.14'}, root=self.root)
        self.assertEqual((row.current, row.latest, row.state), ('2.5.14', '2.5.15', 'outdated'))
        row = sources.release_entry(client, catalog['lychee'] | {'version': '0.24.2'}, root=self.root)
        self.assertEqual((row.current, row.latest, row.state), ('0.24.2', '0.24.2', 'up-to-date'))
        row = sources.release_entry(client, catalog['lychee'] | {'version': '0.24.2+build.1'}, root=self.root)
        self.assertEqual(row.state, 'up-to-date')
        row = sources.release_entry(client, catalog['lychee'] | {'version': 'unreadable'}, root=self.root)
        self.assertEqual((row.state, row.current), ('skipped', 'unreadable'))
        with patch.object(Registry, 'release_status', 503), self.assertRaises(common.Failure):
            sources.release_entry(client, catalog['lychee'], root=self.root)

    def test_nvchecker_native_selection_and_unreadable_sources(self):
        client = self.release_client()
        self.assertEqual(client.release('owner/project'), ('v2.0', 'v2.0'))
        self.assertEqual(client.release('owner/project', tags=True), ('v2.0', 'v2.0'))
        self.assertEqual(client.registry_release({'provider': 'pypi', 'project': 'thing'}), '1.0')
        for tag in ('php-8.5.1', 'lychee-v0.24.2', 'release-without-version', 'v3.0rc1'):
            with (
                self.subTest(tag=tag),
                patch.object(Registry, 'release_tag', tag),
                self.assertRaises(common.UnreadableSource),
            ):
                client.release('owner/project')
        with patch.object(Registry, 'release_tags', ('other/v99.0',)), self.assertRaises(common.UnreadableSource):
            client.release('owner/project', tags=True)
        for status in (401, 403, 404, 503):
            with self.subTest(status=status), patch.object(Registry, 'release_status', status):
                rows = main.guarded('releases', 'fixture', '.', lambda: client.release('owner/project'))
                self.assertEqual([row.state for row in rows], ['error'])

    def test_nvchecker_git_url_is_one_quoted_argument(self):
        repository = self.root / 'repo; touch INJECTED'
        repository.mkdir()
        self.generate(['git', 'init', str(repository)])
        self.generate(
            [
                'git',
                '-C',
                str(repository),
                '-c',
                'user.name=Fixture',
                '-c',
                'user.email=fixture@example.test',
                'commit',
                '--allow-empty',
                '-m',
                'fixture',
            ]
        )
        for tag in ('v1.0', 'v2.0', 'v3.0rc1', 'namespace/v99.0'):
            self.generate(['git', '-C', str(repository), 'tag', tag])
        self.assertEqual(self.release_client().registry_release({'provider': 'git', 'url': str(repository)}), 'v2.0')
        self.assertFalse((self.root / 'INJECTED').exists())

    def test_uv_current_outdated_and_failed_auth_are_distinct(self):
        # The adopter's workspace, optional/group and platform reproduction.
        (self.root / 'pyproject.toml').write_text(
            '[project]\nname="fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
            'dependencies=["member", "packaging==24.0; sys_platform != \'win32\'", '
            '"packaging==25.0; sys_platform == \'win32\'"]\n'
            '[project.optional-dependencies]\nextra=["optional-pkg==24.0"]\n'
            '[dependency-groups]\ndev=["dev-pkg==24.0"]\n'
            '[tool.uv.workspace]\nmembers=["member"]\n'
            '[tool.uv.sources]\nmember={workspace=true}\n'
            f'[[tool.uv.index]]\nname="private"\nurl="{self.registry}/private/simple"\ndefault=true\n'
        )
        (self.root / 'member').mkdir()
        (self.root / 'member/pyproject.toml').write_text(
            '[project]\nname="member"\nversion="0.0.0"\nrequires-python=">=3.11"\ndependencies=["member-pkg==24.0"]\n'
        )
        auth = {
            'UV_INDEX_PRIVATE_USERNAME': 'fixture',
            'UV_INDEX_PRIVATE_PASSWORD': 'fixture-password',
            'UV_KEYRING_PROVIDER': 'disabled',
            'UV_HTTP_RETRIES': '0',
        }
        with patch.dict(os.environ, auth):
            self.generate([settings['uv'], 'lock', '--no-cache', '--no-python-downloads'])
            rows = self.invoke('uv')
        authenticated_rows = rows
        self.assertEqual(
            {(r.name, r.current) for r in rows},
            {
                ('fixture', '0.0.0'),
                ('member', '0.0.0'),
                ('packaging', '24.0'),
                ('packaging', '25.0'),
                ('optional-pkg', '24.0'),
                ('dev-pkg', '24.0'),
                ('member-pkg', '24.0'),
            },
        )
        self.assertTrue(all(r.state == 'outdated' for r in rows if r.current == '24.0'))
        for status in (401, 403, 503):
            with (
                self.subTest(status=status),
                patch.object(Registry, 'denied_status', status),
                patch.dict(os.environ, auth | {'UV_INDEX_PRIVATE_PASSWORD': 'wrong'}),
            ):
                try:
                    rows = self.invoke('uv')
                except common.Failure:
                    continue
                self.assertEqual(common.summary(rows)[1], 2)
                self.assertTrue(any(r.state == 'unknown' for r in rows))
                self.assertFalse(any(r.state == 'up-to-date' for r in rows))
        self.assertEqual(common.summary(authenticated_rows)[1], 2)
        self.assertEqual(next(r.state for r in authenticated_rows if r.current == '25.0'), 'unknown')

    def test_npm_locked_and_registry_config(self):
        self.project()
        self.js(
            'package-lock.json',
            {
                'lockfileVersion': 3,
                'packages': {
                    '': {'name': 'public-provider-fixture'},
                    'node_modules/is-number': {'version': '6.0.0', 'resolved': self.registry + '/is-number-6.0.0.tgz'},
                },
            },
        )
        rows = self.invoke('npm')
        self.assertEqual([(r.current, r.latest, r.state) for r in rows], [('6.0.0', '7.0.0', 'outdated')])
        self.assertFalse((self.root / 'node_modules').exists())

    def test_pnpm_locked_workspace(self):
        self.project()
        shutil.copyfile(fixtures / 'pnpm-lock.yaml', self.root / 'pnpm-lock.yaml')
        rows = self.invoke('pnpm')
        self.assertEqual(rows[0].state, 'outdated')
        self.assertEqual(rows[0].current, '6.0.0')
        self.assertFalse((self.root / 'node_modules').exists())

    def test_pnpm_native_inventory_preserves_aliases_and_non_registry_sources(self):
        self.project()
        (self.root / 'local').mkdir()
        (self.root / 'local/package.json').write_text('{"name":"local","version":"1.0.0"}')
        self.js(
            'package.json',
            {
                'name': 'fixture',
                'version': '1.0.0',
                'dependencies': {
                    'is-number': '7.0.0',
                    'alias': 'npm:is-number@6.0.0',
                    'local': 'file:./local',
                    'linked': 'link:./local',
                    'remote': self.registry + '/is-number-6.0.0.tgz',
                },
            },
        )
        self.generate([settings['pnpm'], 'install', '--lockfile-only', '--ignore-scripts'])
        rows = {row.name: row for row in self.invoke('pnpm') if row.source == 'pnpm-lock.yaml:.'}
        self.assertEqual(rows['is-number'].state, 'up-to-date')
        self.assertEqual(rows['alias'].state, 'outdated')
        self.assertEqual(rows['local'].state, 'unknown')
        self.assertEqual(rows['remote'].state, 'unknown')
        self.assertEqual(rows['linked'].state, 'skipped')
        self.assertFalse((self.root / 'node_modules').exists())

    def test_pnpm_distinct_versions_in_multiple_workspaces(self):
        self.project()
        (self.root / 'pnpm-workspace.yaml').write_text('packages: ["packages/*"]\n')
        lock = yaml.safe_load((fixtures / 'pnpm-lock.yaml').read_text())
        for name, version in [('older', '5.0.0'), ('current', '7.0.0')]:
            folder = self.root / 'packages' / name
            folder.mkdir(parents=True)
            (folder / 'package.json').write_text(
                json.dumps({'name': name, 'version': '0.0.0', 'dependencies': {'is-number': version}})
            )
            lock['importers']['packages/' + name] = {
                'dependencies': {'is-number': {'specifier': version, 'version': version}}
            }
            lock['packages']['is-number@' + version] = lock['packages']['is-number@6.0.0']
            lock['snapshots']['is-number@' + version] = {}
        (self.root / 'pnpm-lock.yaml').write_text(yaml.safe_dump(lock))
        rows = self.invoke('pnpm')
        self.assertEqual({r.current for r in rows if r.state == 'outdated'}, {'5.0.0', '6.0.0'})
        self.assertFalse(any(r.state == 'unknown' for r in rows))
        manifest = json.loads((self.root / 'package.json').read_text())
        manifest['dependencies']['is-number'] = '7.0.0'
        self.js('package.json', manifest)
        lock['importers']['.']['dependencies']['is-number'] = {'specifier': '7.0.0', 'version': '7.0.0'}
        (self.root / 'pnpm-lock.yaml').write_text(yaml.safe_dump(lock))
        rows = self.invoke('pnpm')
        self.assertEqual({r.current for r in rows if r.state == 'outdated'}, {'5.0.0'})
        self.assertFalse(any(r.state == 'unknown' for r in rows))

    def test_npm_aliases_nested_versions_and_source_identity(self):
        self.project()
        manifest = json.loads((self.root / 'package.json').read_text())
        manifest['dependencies'] = {'alias': 'npm:is-number@6.0.0', 'parent': 'git+https://example.invalid/repo'}
        self.js('package.json', manifest)
        self.js(
            'package-lock.json',
            {
                'lockfileVersion': 3,
                'packages': {
                    '': {
                        'name': 'public-provider-fixture',
                        'dependencies': {'alias': 'npm:is-number@6.0.0', 'parent': '1.0.0'},
                    },
                    'node_modules/alias': {
                        'name': 'is-number',
                        'version': '6.0.0',
                        'resolved': self.registry + '/is-number-6.0.0.tgz',
                    },
                    'node_modules/parent': {
                        'version': '1.0.0',
                        'resolved': 'git+https://example.invalid/repo',
                        'dependencies': {'is-number': '5.0.0'},
                    },
                    'node_modules/parent/node_modules/is-number': {
                        'version': '5.0.0',
                        'resolved': self.registry + '/is-number-5.0.0.tgz',
                    },
                },
            },
        )
        rows = self.invoke('npm')
        self.assertEqual({r.current for r in rows if r.state == 'outdated'}, {'5.0.0', '6.0.0'})
        lock = json.loads((self.root / 'package-lock.json').read_text())
        lock['packages']['node_modules/alias']['resolved'] = self.registry + '/different-source.tgz'
        self.js('package-lock.json', lock)
        rows = self.invoke('npm')
        self.assertEqual(next(r for r in rows if r.source.endswith('/alias')).state, 'unknown')

    def test_upstream_semver_build_metadata_and_prereleases(self):
        report = self.root / 'versions.json'
        report.write_text(
            json.dumps(
                [
                    {'name': 'build', 'current': '1.0.0+build.2', 'latest': '1.0.0+build.1'},
                    {'name': 'pre', 'current': '1.0.0-beta.2', 'latest': '1.0.0-beta.11'},
                    {'name': 'ahead', 'current': '3.0.0', 'latest': '2.0.0'},
                ]
            )
        )
        document = common.command_json([settings['semver'], 'versions', str(report)], self.root, 10)
        self.assertEqual([r['state'] for r in document['results']], ['up-to-date', 'outdated', 'ahead'])

    def test_npm_invalid_lock_and_empty_project(self):
        self.project()
        (self.root / 'package-lock.json').write_text('broken JSON')
        with self.assertRaises(common.Failure):
            self.invoke('npm')
        self.js('package.json', {'name': 'empty', 'version': '1.0.0'})
        self.js('package-lock.json', {'lockfileVersion': 3, 'packages': {'': {'name': 'empty', 'version': '1.0.0'}}})
        self.assertEqual(self.invoke('npm')[0].state, 'up-to-date')

    def test_yarn_generation_lock_formats(self):
        for generation, lock_version in ((2, 4), (3, 6), (4, 10)):
            with self.subTest(generation=generation):
                self.project(f'yarn@{generation}.0.0')
                (self.root / '.yarnrc.yml').write_text(
                    f'npmRegistryServer: "{self.registry}"\nunsafeHttpWhitelist: [localhost]\n'
                )
                (self.root / 'yarn.lock').write_text(
                    (fixtures / 'yarn.lock').read_text().replace('version: 10\n', f'version: {lock_version}\n')
                )
                rows = self.invoke('yarn')
                self.assertEqual([r.state for r in rows], ['outdated', 'skipped'])
                self.assertEqual(rows[0].latest, '7.0.0')
                self.assertFalse((self.root / '.pnp.cjs').exists())

    def test_yarn_aliases_ranges_virtuals_and_unsupported_protocols(self):
        self.project('yarn@4.0.0')
        (self.root / '.yarnrc.yml').write_text(
            f'npmRegistryServer: "{self.registry}"\nunsafeHttpWhitelist: [localhost]\n'
        )
        lock = yaml.safe_load((fixtures / 'yarn.lock').read_text())
        item = lock.pop('is-number@npm:6.0.0')
        lock['alias@npm:is-number@^6.0.0, is-number@npm:~6.0.0'] = item
        lock['is-number@virtual:abc#npm:6.0.0'] = dict(item, resolution='is-number@virtual:abc#npm:6.0.0')
        lock['patched@patch:abc'] = dict(item, resolution='patched@patch:abc')
        (self.root / 'yarn.lock').write_text(yaml.safe_dump(lock))
        rows = self.invoke('yarn')
        self.assertEqual(len([r for r in rows if r.state == 'outdated']), 2)
        self.assertEqual(next(r for r in rows if r.name == 'is-number@npm:6.0.0').compatible, '6.0.0')
        self.assertEqual(next(r for r in rows if r.name == 'patched@patch:abc').state, 'unknown')

    def test_yarn_classic_and_malformed_locks_fail(self):
        self.project('yarn@4.0.0')
        for lock in ('# yarn lockfile v1\n', '__metadata: {version: 8}\nbroken: true\n', '__metadata: {version: 8}\n'):
            (self.root / 'yarn.lock').write_text(lock)
            with self.subTest(lock=lock), self.assertRaises(common.Failure):
                self.invoke('yarn')

    def test_nix_inventory_uses_native_metadata_and_custom_lock(self):
        (self.root / 'flake.nix').write_text(
            '{ inputs.source.url = "github:example/example/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; outputs = inputs: {}; }'
        )
        self.js(
            'custom.lock',
            {
                'version': 7,
                'root': 'root',
                'nodes': {
                    'root': {'inputs': {'source': 'source'}},
                    'source': {
                        'locked': {
                            'type': 'github',
                            'owner': 'example',
                            'repo': 'example',
                            'rev': 'a' * 40,
                            'narHash': 'sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
                        },
                        'original': {'type': 'github', 'owner': 'example', 'repo': 'example', 'rev': 'a' * 40},
                    },
                },
            },
        )
        before = digest(self.root)
        with tempfile.TemporaryDirectory() as store, patch.dict('os.environ', {'NIX_REMOTE': f'local?root={store}'}):
            jobs = list(
                sources.nix_jobs({'exe': settings['nix'], 'lockFile': 'custom.lock'}, self.root, network.Client())
            )
        self.assertEqual([callback().state for _, callback in jobs], ['pinned'])
        self.assertEqual(before, digest(self.root))

    def test_uv_locked_universal_custom_index(self):
        (self.root / 'pyproject.toml').write_text(
            '[project]\nname="nix-tools-public-provider-fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\ndependencies=["packaging==24.0"]\n'
            + f'[[tool.uv.index]]\nurl="{self.registry}/simple"\ndefault=true\n'
        )
        (self.root / 'uv.lock').write_text(
            (fixtures / 'uv.lock').read_text().replace('https://pypi.org/simple', self.registry + '/simple')
        )
        rows = self.invoke('uv')
        self.assertEqual([r.state for r in rows], ['skipped', 'outdated'])
        self.assertEqual(rows[-1].latest, '25.0')
        self.assertFalse((self.root / '.venv').exists())

    def test_uv_native_inventory_includes_optional_groups_and_platforms(self):
        (self.root / 'pyproject.toml').write_text(
            '[project]\nname="fixture"\nversion="0.0.0"\nrequires-python=">=3.11"\n'
            'dependencies=["packaging==24.0; sys_platform != \'win32\'", "packaging==25.0; sys_platform == \'win32\'"]\n'
            '[project.optional-dependencies]\nextra=["optional-pkg==24.0"]\n'
            '[dependency-groups]\ncheck=["group-pkg==24.0"]\n'
            f'[[tool.uv.index]]\nurl="{self.registry}/simple"\ndefault=true\n'
        )
        self.generate([settings['uv'], 'lock', '--no-cache', '--no-python-downloads'])
        rows = self.invoke('uv')
        self.assertEqual({row.current for row in rows if row.name == 'packaging'}, {'24.0', '25.0'})
        self.assertEqual({row.name for row in rows}, {'fixture', 'packaging', 'optional-pkg', 'group-pkg'})
        self.assertEqual(next(row for row in rows if row.name == 'optional-pkg').state, 'outdated')

    def test_composer_locked_with_private_repository_semantics(self):
        self.js(
            'composer.json',
            {
                'name': 'fixture/public',
                'require': {'psr/log': '1.1.4'},
                'config': {'secure-http': False},
                'repositories': [{'type': 'composer', 'url': self.registry + '/composer'}, {'packagist.org': False}],
            },
        )
        self.js(
            'composer.lock',
            {
                'packages': [{'name': 'psr/log', 'version': '1.1.4', 'type': 'library'}],
                'packages-dev': [],
                'aliases': [],
                'minimum-stability': 'stable',
                'stability-flags': {},
                'prefer-stable': False,
                'prefer-lowest': False,
                'platform': {},
                'platform-dev': {},
            },
        )
        rows = self.invoke('composer')
        self.assertEqual([(r.current, r.latest, r.state) for r in rows], [('1.1.4', '3.0.2', 'outdated')])
        self.assertFalse((self.root / 'vendor').exists())

    def test_cargo_locked_with_source_replacement(self):
        (self.root / 'src').mkdir()
        (self.root / 'src/lib.rs').write_text('')
        (self.root / 'Cargo.toml').write_text(
            '[package]\nname="fixture"\nversion="0.0.0"\nedition="2021"\n[dependencies]\nsemver="=1.0.20"\n'
        )
        checksum = hashlib.sha256(crate('1.0.20')).hexdigest()
        (self.root / 'Cargo.lock').write_text(
            'version=4\n[[package]]\nname="fixture"\nversion="0.0.0"\ndependencies=["semver"]\n[[package]]\nname="semver"\nversion="1.0.20"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
            + f'checksum="{checksum}"\n'
        )
        (self.root / '.cargo').mkdir()
        (self.root / '.cargo/config.toml').write_text(
            '[source.crates-io]\nreplace-with="fixture"\n[source.fixture]\n'
            + f'registry="sparse+{self.registry}/index/"\n'
        )
        rows = self.invoke('cargo')
        self.assertEqual(rows[0].state, 'outdated')
        self.assertEqual(rows[0].latest, '1.0.21')
        self.assertFalse((self.root / 'target').exists())


if __name__ == '__main__':
    unittest.main()
