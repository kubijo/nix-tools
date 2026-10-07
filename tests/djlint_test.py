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
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast, override

import jinja2


class Settings(TypedDict):
    variants: dict[str, dict[str, str]]
    profiles: list[str]
    languages: dict[str, dict[str, str]]
    node: str
    renderScript: str
    nunjucks: str
    badPolicies: list[dict[str, str]]
    badOptions: list[dict[str, str]]


class CoverageRow(TypedDict):
    path: str
    format: list[dict[str, str]]
    lint: list[dict[str, str]]


class Coverage(TypedDict):
    files: list[CoverageRow]


SETTINGS = cast(Settings, json.loads(pathlib.Path(sys.argv.pop(1)).read_text()))
FIXTURES = pathlib.Path(sys.argv.pop(1))


class Templates(unittest.TestCase):
    directory: tempfile.TemporaryDirectory[str]
    root: pathlib.Path
    tool_log: pathlib.Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.tool_log = self.root / '.tmp/tool.log'

    @override
    def setUp(self) -> None:
        (self.root / '.fixture-root').touch()
        (self.root / '.tmp').mkdir()

    def write(self, name: str, content: str) -> pathlib.Path:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(content)
        return target

    def assert_paragraph_indent(self, path: pathlib.Path, spaces: int) -> None:
        expected = '\n' + ' ' * spaces + '<p>'
        self.assertIn(expected, path.read_text())

    def run_tool(
        self,
        phase: str,
        variant: str = 'base',
        success: bool = True,
        arguments: Sequence[str] = (),
        programs: Mapping[str, str] | None = None,
    ) -> str:
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

    def test_profiles(self) -> None:
        fixtures = {path.stem: path for path in (FIXTURES / 'profiles').glob('*.input')}
        self.assertEqual(set(fixtures), set(SETTINGS['profiles']))
        for profile, fixture in fixtures.items():
            with self.subTest(profile=profile):
                target = self.write('example.template', fixture.read_text())
                programs = SETTINGS['languages'][profile]
                _ = self.run_tool('format', programs=programs)
                self.assertEqual(target.read_bytes(), fixture.with_suffix('.expected').read_bytes())
                _ = self.run_tool('format', programs=programs, arguments=('--ci', '--no-cache'))
                _ = self.run_tool('lint', programs=programs)
                _ = target.write_text('<img src="photo.png">\n')
                output = self.run_tool('lint', programs=programs, success=False)
                self.assertIn('H013', output)

    def test_nunjucks_unclosed_switch_still_fails(self) -> None:
        source = (FIXTURES / 'profiles/nunjucks.input').read_text().replace('{% endswitch %}', '')
        _ = self.write('broken.html.njk', source)
        output = self.run_tool('lint', success=False)
        self.assertIn('T038', output)

    def test_options_cannot_inject_excluded_files(self) -> None:
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

    def test_delimiter_is_a_valid_selected_filename(self) -> None:
        target = self.write('--', '<div><p>Original</p></div>\n')
        _ = self.run_tool('format', variant='delimiterFile')
        self.assertEqual(target.read_text(), '<div>\n    <p>Original</p>\n</div>\n')
        _ = self.run_tool('lint', variant='delimiterFile')

    def test_nunjucks_expression_values_survive_formatting(self) -> None:
        templates: dict[str, str] = {}
        for index, expression in enumerate((r'"\u0041"', r'"\x41"', r'"a\nb"', r'"C:\\folder"')):
            templates[f'set-{index}.html.njk'] = '{% set value = ' + expression + ' %}<p>{{ value }}</p>\n'
            templates[f'call-{index}.html.njk'] = (
                '{% macro identity(x) %}{{ x }}{% endmacro %}<p>{{ identity(' + expression + ') }}</p>\n'
            )
        expected = {name: self.render_nunjucks(templates, name, [{}]) for name in templates}
        for variant in ('base', 'json', 'pyproject', 'unsafeExpressions'):
            with self.subTest(variant=variant):
                for name, source in templates.items():
                    _ = self.write(name, source)
                _ = self.run_tool('format', variant=variant, arguments=('--no-cache',))
                formatted = {name: (self.root / name).read_text() for name in templates}
                for name in templates:
                    self.assertEqual(self.render_nunjucks(formatted, name, [{}]), expected[name], name)
                _ = self.run_tool('format', variant=variant, arguments=('--ci', '--no-cache'))
                _ = self.run_tool('lint')

    def test_unusual_names_through_both_runners(self) -> None:
        target = self.write("nested 'quoted; café/-braces{[x]}, file.html.njk", '<div><p>Content</p></div>\n')
        _ = self.run_tool('format')
        self.assertEqual(target.read_text(), '<div>\n    <p>Content</p>\n</div>\n')
        _ = self.run_tool('lint')
        _ = target.write_text('<img src="bad.png">\n')
        output = self.run_tool('lint', success=False)
        self.assertIn(target.name, output)

    def render_nunjucks(
        self, templates: Mapping[str, str], name: str, contexts: Sequence[Mapping[str, object]]
    ) -> list[str]:
        result = subprocess.run(
            [SETTINGS['node'], SETTINGS['renderScript'], SETTINGS['nunjucks']],
            input=json.dumps({'templates': templates, 'name': name, 'contexts': contexts}),
            capture_output=True,
            text=True,
            check=True,
        )
        return cast(list[str], json.loads(result.stdout))

    def test_native_nunjucks_rendering(self) -> None:
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
            _ = self.write(name, source)
        _ = self.run_tool('format')
        _ = self.run_tool('format', arguments=('--ci', '--no-cache'))
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
            _ = self.render_nunjucks({'undefined.html.njk': '<p>{{ missing }}</p>'}, 'undefined.html.njk', [{}])

    def test_render_and_idempotence(self) -> None:
        environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=True)
        contexts = [
            {'name': name, 'enabled': enabled, 'details': '  exact\n <>&\n  '}
            for name in ('', 'Alice & Bob')
            for enabled in (False, True)
        ]
        for fixture in FIXTURES.glob('*.njk'):
            _ = shutil.copyfile(fixture, self.root / fixture.name)
        original = {p.name: p.read_text() for p in self.root.glob('*.njk')}
        _ = self.run_tool('format')
        _ = self.run_tool('format', arguments=('--ci', '--no-cache'))
        _ = self.run_tool('lint')
        for name, before in original.items():
            after = (self.root / name).read_text()
            for context in contexts:
                expected = environment.from_string(before).render(context)
                actual = environment.from_string(after).render(context)

                # HTML block indentation may change; pre content and explicitly
                # whitespace-controlled output must stay byte-for-byte intact.
                def normalize(text: str) -> str:
                    return re.sub(r'>\s+<', '><', text).strip()

                self.assertEqual(normalize(actual), normalize(expected))
                if name == 'control.html.njk':
                    self.assertEqual(actual.strip(), expected.strip())
                self.assertEqual(
                    re.findall(r'<pre>(.*?)</pre>', actual, re.DOTALL),
                    re.findall(r'<pre>(.*?)</pre>', expected, re.DOTALL),
                )

    def test_lint_second_file_and_immutability(self) -> None:
        _ = self.write('a.html.njk', '<p>Good</p>\n')
        target = self.write('z.html.njk', '<img src="broken.png">\n')
        before = target.read_bytes()
        for variant in ('base', 'perFile', 'overrides'):
            output = self.run_tool('lint', variant=variant, success=False)
            self.assertIn('checked 2 files', output)
            self.assertIn('z.html.njk', output)
            self.assertIn('H013', output)
            self.assertEqual(target.read_bytes(), before)

    def test_config_and_cache(self) -> None:
        target = self.write('sample.html.njk', '<div><p>Nested</p></div>\n')
        target.chmod(0o755)
        _ = self.run_tool('format')
        self.assert_paragraph_indent(target, 4)
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
            _ = self.run_tool('format', variant=variant)
            self.assert_paragraph_indent(target, 4 if variant == 'base' else 2)
            _ = self.run_tool('format', variant=variant, arguments=('--ci', '--no-cache'))
        self.assertTrue(os.access(target, os.X_OK))

    def test_tool_override_profile_and_cache(self) -> None:
        _ = self.write('example.html.njk', '<div><p>Nested</p></div>\n')
        _ = self.run_tool('format')
        _ = self.run_tool('format', variant='overrides')
        before = self.tool_log.read_text()
        self.assertIn('--reformat\n--profile\nnunjucks\n', before)
        _ = self.run_tool('format', variant='overrides')
        self.assertEqual(self.tool_log.read_text(), before)
        _ = self.run_tool('lint', variant='overrides')
        self.assertIn('--lint\n--profile\nnunjucks\n', self.tool_log.read_text())

    def test_failed_formatter_preserves_source(self) -> None:
        target = self.write('example.html.njk', '<div><p>Original</p></div>\n')
        original = target.read_bytes()
        for variant in ('failedTool', 'emptyTool'):
            _ = self.run_tool('format', variant=variant, success=False)
            self.assertEqual(target.read_bytes(), original)

    def test_ambient_policy_isolation(self) -> None:
        target = self.write('sample.html.njk', '<div><p>Nested</p></div>\n')
        for name, content in {
            'pyproject.toml': '[tool.djlint]\nindent = 13\nignore = "H013"\n',
            '.djlintrc': '{"indent":13,"ignore":"H013"}',
            'djlint.toml': 'indent = 13\nignore = "H013"\n',
            '.djlint.toml': 'indent = 13\nignore = "H013"\n',
            '.editorconfig': 'root = true\n[*]\nindent_size = 13\n',
            '.djlint_rules.yaml': 'not valid rules',
        }.items():
            _ = self.write(name, content)
        _ = self.run_tool('format', arguments=('sample.html.njk',))
        self.assert_paragraph_indent(target, 4)
        _ = target.write_text('<img src="missing.png">\n')
        _ = self.run_tool('lint', success=False)
        _ = self.write('nested/.djlintrc', '{"ignore":"H013"}')
        _ = target.rename(self.root / 'nested/z.html.njk')
        _ = self.run_tool('lint', success=False)
        _ = self.run_tool('lint', variant='ignore')

    def test_reject_bypass_and_bad_configuration(self) -> None:
        target = self.write('sample.html.njk', '<div><img src="bad.png"></div>\n')
        before = target.read_bytes()
        for group in ('badPolicies', 'badOptions'):
            for index, programs in enumerate(SETTINGS[group]):
                for phase in ('format', 'lint'):
                    with self.subTest(group=group, index=index, phase=phase):
                        output = self.run_tool(phase, programs=programs, success=False)
                        self.assertIn('djLint:', output)
                        self.assertEqual(target.read_bytes(), before)

    def test_independent_entries_and_extensionless(self) -> None:
        for filename in ('one.html.njk', 'two.html.j2', 'three.html.dtl'):
            _ = self.write(filename, '<div><p>Nested</p></div>\n')
        _ = self.run_tool('format', variant='independent')
        self.assert_paragraph_indent(self.root / 'one.html.njk', 4)
        self.assert_paragraph_indent(self.root / 'two.html.j2', 2)
        self.assert_paragraph_indent(self.root / 'three.html.dtl', 2)
        _ = self.write('two.html.j2', '<img src="bad.png">\n')
        output = self.run_tool('lint', variant='independent')
        self.assertIn('checked 2 files', output)
        for path in self.root.glob('*.html.*'):
            path.unlink()
        _ = self.write('nested/template', '<div><p>Nested</p></div>\n')
        _ = self.run_tool('format', variant='extensionless')
        _ = self.run_tool('lint', variant='extensionless')

    def test_scope_and_coverage(self) -> None:
        _ = self.write('good.html.njk', '<p>Good</p>\n')
        for name in ('skip.html.njk', 'lint.html.njk', 'local.html.njk'):
            _ = self.write(name, '<img src="bad.png">\n')
        _ = self.write('format.html.njk', '<p>Fine</p>\n')
        output = self.run_tool('lint', variant='exclude')
        self.assertIn('checked 2 files', output)
        # Excluding only a formatter leaves an unmatched file, deliberately fatal.
        _ = self.run_tool('format', variant='exclude', success=False)
        _ = subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        _ = subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        report = cast(Coverage, json.loads(self.run_tool('coverage', variant='exclude', arguments=('--json',))))
        files = {entry['path']: entry for entry in report['files']}
        self.assertEqual(files['good.html.njk']['format'], [{'name': 'nunjucks', 'kind': 'format'}])
        self.assertEqual(files['good.html.njk']['lint'], [{'name': 'nunjucks', 'kind': 'semantic'}])
        self.assertEqual(files['local.html.njk']['format'], [])
        self.assertEqual(files['local.html.njk']['lint'], [])
        self.assertEqual(files['skip.html.njk']['format'], [])
        self.assertEqual(files['skip.html.njk']['lint'], [])
        self.assertEqual(files['format.html.njk']['format'], [])
        self.assertEqual(files['lint.html.njk']['lint'], [])

    def test_arbitrary_jinja_is_unmatched(self) -> None:
        _ = self.write('state.j2', '{% if enabled %}\nkey: value\n{% endif %}\n')
        _ = self.run_tool('format', success=False)


if __name__ == '__main__':
    _ = unittest.main()
