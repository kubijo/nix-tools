"""Behavioral checks through the public configure API and the real pinned djLint."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import jinja2

SETTINGS = json.loads(pathlib.Path(sys.argv.pop(1)).read_text())
FIXTURES = pathlib.Path(sys.argv.pop(1))


class Templates(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        (self.root / '.fixture-root').touch()
        (self.root / '.tmp').mkdir()
        self.tool_log = self.root / '.tmp/tool.log'

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def run_tool(self, phase, variant='base', success=True, arguments=(), programs=None):
        programs = programs or SETTINGS['variants'][variant]
        result = subprocess.run(
            [programs[phase], *arguments],
            cwd=self.root,
            env=os.environ | {'NIX_TOOLS_DJLINT_LOG': str(self.tool_log)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result.stdout if phase == 'coverage' else result.stdout + result.stderr

    def test_profiles(self):
        fixtures = {path.stem: path for path in (FIXTURES / 'profiles').glob('*.input')}
        self.assertEqual(set(fixtures), set(SETTINGS['profiles']))
        for profile, fixture in fixtures.items():
            with self.subTest(profile=profile):
                target = self.write('example.template', fixture.read_text())
                programs = SETTINGS['languages'][profile]
                self.run_tool('format', programs=programs)
                self.assertEqual(target.read_bytes(), fixture.with_suffix('.expected').read_bytes())
                self.run_tool('format', programs=programs, arguments=('--ci', '--no-cache'))
                self.run_tool('lint', programs=programs)
                target.write_text('<img src="photo.png">\n')
                output = self.run_tool('lint', programs=programs, success=False)
                self.assertIn('H013', output)

    def test_nunjucks_unclosed_switch_still_fails(self):
        source = (FIXTURES / 'profiles/nunjucks.input').read_text().replace('{% endswitch %}', '')
        self.write('broken.html.njk', source)
        output = self.run_tool('lint', success=False)
        self.assertIn('T038', output)

    def test_options_cannot_inject_excluded_files(self):
        files = {
            name: self.write(name, '<div><p>Original</p></div>\n')
            for name in ('--', 'outside.txt', 'a.html.njk', 'z.html.njk')
        }
        original = {name: path.read_bytes() for name, path in files.items()}
        for variant, phase in (('delimiter', 'format'), ('delimiter', 'lint'), ('delimiterPerFile', 'lint')):
            with self.subTest(variant=variant, phase=phase):
                output = self.run_tool(phase, variant=variant, success=False)
                self.assertIn('unsupported djLint option', output)
                self.assertEqual({name: path.read_bytes() for name, path in files.items()}, original)

    def test_delimiter_is_a_valid_selected_filename(self):
        target = self.write('--', '<div><p>Original</p></div>\n')
        self.run_tool('format', variant='delimiterFile')
        self.assertEqual(target.read_text(), '<div>\n    <p>Original</p>\n</div>\n')
        self.run_tool('lint', variant='delimiterFile')

    def test_nunjucks_expression_values_survive_formatting(self):
        templates = {}
        for index, expression in enumerate((r'"\u0041"', r'"\x41"', r'"a\nb"', r'"C:\\folder"')):
            templates[f'set-{index}.html.njk'] = '{% set value = ' + expression + ' %}<p>{{ value }}</p>\n'
            templates[f'call-{index}.html.njk'] = (
                '{% macro identity(x) %}{{ x }}{% endmacro %}<p>{{ identity(' + expression + ') }}</p>\n'
            )
        expected = {name: self.render_nunjucks(templates, name, [{}]) for name in templates}
        for variant in ('base', 'json', 'pyproject', 'unsafeExpressions'):
            with self.subTest(variant=variant):
                for name, source in templates.items():
                    self.write(name, source)
                self.run_tool('format', variant=variant, arguments=('--no-cache',))
                formatted = {name: (self.root / name).read_text() for name in templates}
                for name in templates:
                    self.assertEqual(self.render_nunjucks(formatted, name, [{}]), expected[name], name)
                self.run_tool('format', variant=variant, arguments=('--ci', '--no-cache'))
                self.run_tool('lint')

    def test_unusual_names_through_both_runners(self):
        target = self.write("nested 'quoted; café/-braces{[x]}, file.html.njk", '<div><p>Content</p></div>\n')
        self.run_tool('format')
        self.assertEqual(target.read_text(), '<div>\n    <p>Content</p>\n</div>\n')
        self.run_tool('lint')
        target.write_text('<img src="bad.png">\n')
        output = self.run_tool('lint', success=False)
        self.assertIn(target.name, output)

    def render_nunjucks(self, templates, name, contexts):
        result = subprocess.run(
            [SETTINGS['node'], SETTINGS['renderScript'], SETTINGS['nunjucks']],
            input=json.dumps({'templates': templates, 'name': name, 'contexts': contexts}),
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_native_nunjucks_rendering(self):
        templates = {path.name: path.read_text() for path in FIXTURES.glob('*.njk')}
        templates['switch.html.njk'] = (FIXTURES / 'profiles/nunjucks.input').read_text()
        templates['base.html.njk'] = '<main>{% block content %}fallback{% endblock %}</main>\n'
        templates['macros.html.njk'] = '{% macro label(value) %}<b>{{ value | escape }}</b>{% endmacro %}\n'
        templates['child.html.njk'] = (
            '{% extends "base.html.njk" %}{% from "macros.html.njk" import label %}'
            '{% block content %}<p>{{ label(name) }}</p>{% include "control.html.njk" %}{% endblock %}\n'
        )
        contexts = [
            {'state': state, 'name': name, 'enabled': enabled, 'items': items, 'details': '  <>&\n  exact  '}
            for state in ('ready', 'waiting')
            for name in ('', '<unsafe>&"')
            for enabled in (False, True)
            for items in ([], [{'label': '<item>'}])
        ]
        for name, source in templates.items():
            self.write(name, source)
        self.run_tool('format')
        self.run_tool('format', arguments=('--ci', '--no-cache'))
        formatted = {name: (self.root / name).read_text() for name in templates}
        for name in ('fragment.html.njk', 'control.html.njk', 'switch.html.njk', 'child.html.njk'):
            before = self.render_nunjucks(templates, name, contexts)
            after = self.render_nunjucks(formatted, name, contexts)
            for expected, actual in zip(before, after, strict=True):
                with self.subTest(name=name, expected=expected):
                    self.assertEqual(re.sub(r'>\s+<', '><', actual).strip(), re.sub(r'>\s+<', '><', expected).strip())
                    self.assertEqual(
                        re.findall(r'<pre>(.*?)</pre>', actual, re.DOTALL),
                        re.findall(r'<pre>(.*?)</pre>', expected, re.DOTALL),
                    )
                    if name == 'control.html.njk':
                        self.assertEqual(actual.strip(), expected.strip())
        with self.assertRaises(subprocess.CalledProcessError):
            self.render_nunjucks({'undefined.html.njk': '<p>{{ missing }}</p>'}, 'undefined.html.njk', [{}])

    def test_render_and_idempotence(self):
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=True)
        contexts = [
            {'name': name, 'enabled': enabled, 'details': '  exact\n <>&\n  '}
            for name in ('', 'Alice & Bob')
            for enabled in (False, True)
        ]
        for fixture in FIXTURES.glob('*.njk'):
            shutil.copyfile(fixture, self.root / fixture.name)
        original = {p.name: p.read_text() for p in self.root.glob('*.njk')}
        self.run_tool('format')
        self.run_tool('format', arguments=('--ci', '--no-cache'))
        self.run_tool('lint')
        for name, before in original.items():
            after = (self.root / name).read_text()
            for context in contexts:
                expected = environment.from_string(before).render(context)
                actual = environment.from_string(after).render(context)
                # HTML block indentation may change; pre content and explicitly
                # whitespace-controlled output must stay byte-for-byte intact.
                normalize = lambda text: re.sub(r'>\s+<', '><', text).strip()
                self.assertEqual(normalize(actual), normalize(expected))
                if name == 'control.html.njk':
                    self.assertEqual(actual.strip(), expected.strip())
                self.assertEqual(
                    re.findall(r'<pre>(.*?)</pre>', actual, re.DOTALL),
                    re.findall(r'<pre>(.*?)</pre>', expected, re.DOTALL),
                )

    def test_lint_second_file_and_immutability(self):
        self.write('a.html.njk', '<p>Good</p>\n')
        target = self.write('z.html.njk', '<img src="broken.png">\n')
        before = target.read_bytes()
        for variant in ('base', 'perFile', 'overrides'):
            output = self.run_tool('lint', variant=variant, success=False)
            self.assertIn('checked 2 files', output)
            self.assertIn('z.html.njk', output)
            self.assertIn('H013', output)
            self.assertEqual(target.read_bytes(), before)

    def test_config_and_cache(self):
        target = self.write('sample.html.njk', '<div><p>Nested</p></div>\n')
        target.chmod(0o755)
        self.run_tool('format')
        self.assertIn('\n    <p>', target.read_text())
        for variant in (
            'indent2',
            'base',
            'json',
            'base',
            'pyproject',
            'base',
            'flatSuffix',
            'base',
            'contextual',
            'base',
            'subpath',
            'base',
            'extra',
        ):
            self.run_tool('format', variant=variant)
            indent = '    ' if variant == 'base' else '  '
            self.assertIn('\n' + indent + '<p>', target.read_text())
            self.run_tool('format', variant=variant, arguments=('--ci', '--no-cache'))
        self.assertTrue(os.access(target, os.X_OK))

    def test_tool_override_profile_and_cache(self):
        self.write('example.html.njk', '<div><p>Nested</p></div>\n')
        self.run_tool('format')
        self.run_tool('format', variant='overrides')
        before = self.tool_log.read_text()
        self.assertIn('--reformat\n--profile\nnunjucks\n', before)
        self.run_tool('format', variant='overrides')
        self.assertEqual(self.tool_log.read_text(), before)
        self.run_tool('lint', variant='overrides')
        self.assertIn('--lint\n--profile\nnunjucks\n', self.tool_log.read_text())

    def test_failed_formatter_preserves_source(self):
        target = self.write('example.html.njk', '<div><p>Original</p></div>\n')
        original = target.read_bytes()
        for variant in ('failedTool', 'emptyTool'):
            self.run_tool('format', variant=variant, success=False)
            self.assertEqual(target.read_bytes(), original)

    def test_ambient_policy_isolation(self):
        target = self.write('sample.html.njk', '<div><p>Nested</p></div>\n')
        for name, content in {
            'pyproject.toml': '[tool.djlint]\nindent = 13\nignore = "H013"\n',
            '.djlintrc': '{"indent":13,"ignore":"H013"}',
            'djlint.toml': 'indent = 13\nignore = "H013"\n',
            '.djlint.toml': 'indent = 13\nignore = "H013"\n',
            '.editorconfig': 'root = true\n[*]\nindent_size = 13\n',
            '.djlint_rules.yaml': 'not valid rules',
        }.items():
            self.write(name, content)
        self.run_tool('format', arguments=('sample.html.njk',))
        self.assertIn('\n    <p>', target.read_text())
        target.write_text('<img src="missing.png">\n')
        self.run_tool('lint', success=False)
        self.write('nested/.djlintrc', '{"ignore":"H013"}')
        target.rename(self.root / 'nested/z.html.njk')
        self.run_tool('lint', success=False)
        self.run_tool('lint', variant='ignore')

    def test_reject_bypass_and_bad_configuration(self):
        target = self.write('sample.html.njk', '<div><img src="bad.png"></div>\n')
        before = target.read_bytes()
        for group in ('badPolicies', 'badOptions'):
            for index, programs in enumerate(SETTINGS[group]):
                for phase in ('format', 'lint'):
                    with self.subTest(group=group, index=index, phase=phase):
                        output = self.run_tool(phase, programs=programs, success=False)
                        self.assertIn('djLint:', output)
                        self.assertEqual(target.read_bytes(), before)

    def test_independent_entries_and_extensionless(self):
        for filename in ('one.html.njk', 'two.html.j2', 'three.html.dtl'):
            self.write(filename, '<div><p>Nested</p></div>\n')
        self.run_tool('format', variant='independent')
        self.assertIn('\n    <p>', (self.root / 'one.html.njk').read_text())
        self.assertIn('\n  <p>', (self.root / 'two.html.j2').read_text())
        self.assertIn('\n  <p>', (self.root / 'three.html.dtl').read_text())
        self.write('two.html.j2', '<img src="bad.png">\n')
        output = self.run_tool('lint', variant='independent')
        self.assertIn('checked 2 files', output)
        for path in self.root.glob('*.html.*'):
            path.unlink()
        self.write('nested/template', '<div><p>Nested</p></div>\n')
        self.run_tool('format', variant='extensionless')
        self.run_tool('lint', variant='extensionless')

    def test_scope_and_coverage(self):
        self.write('good.html.njk', '<p>Good</p>\n')
        for name in ('skip.html.njk', 'lint.html.njk', 'local.html.njk'):
            self.write(name, '<img src="bad.png">\n')
        self.write('format.html.njk', '<p>Fine</p>\n')
        output = self.run_tool('lint', variant='exclude')
        self.assertIn('checked 2 files', output)
        # Excluding only a formatter leaves an unmatched file, deliberately fatal.
        self.run_tool('format', variant='exclude', success=False)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        report = json.loads(self.run_tool('coverage', variant='exclude', arguments=('--json',)))
        files = {entry['path']: entry for entry in report['files']}
        self.assertEqual(files['good.html.njk']['format'], [{'name': 'nunjucks', 'kind': 'format'}])
        self.assertEqual(files['good.html.njk']['lint'], [{'name': 'nunjucks', 'kind': 'semantic'}])
        self.assertEqual(files['local.html.njk']['format'], [])
        self.assertEqual(files['local.html.njk']['lint'], [])
        self.assertEqual(files['skip.html.njk']['format'], [])
        self.assertEqual(files['skip.html.njk']['lint'], [])
        self.assertEqual(files['format.html.njk']['format'], [])
        self.assertEqual(files['lint.html.njk']['lint'], [])

    def test_arbitrary_jinja_is_unmatched(self):
        self.write('state.j2', '{% if enabled %}\nkey: value\n{% endif %}\n')
        self.run_tool('format', success=False)


if __name__ == '__main__':
    unittest.main()
