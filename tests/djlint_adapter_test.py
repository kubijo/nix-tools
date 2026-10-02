"""Exercise the adapter's CLI boundaries and failure behavior with branch coverage."""

import json
import os
import pathlib
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

ADAPTER = sys.argv.pop(1)
TOOL = sys.argv.pop(1)


class Adapter(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.source = self.root / 'input.html.njk'
        self.source.write_text('<div><p>Original</p></div>\n')
        self.policy = self.root / 'policy.json'
        self.policy.write_text('{}')

    def invoke(
        self,
        mode='format',
        options=(),
        files=None,
        syntax='json',
        tool=TOOL,
        success=True,
        environment=None,
        profile='nunjucks',
        option_count=None,
        separator='--',
    ):
        original = self.source.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'coverage',
                'run',
                '--branch',
                '--parallel-mode',
                f'--include={ADAPTER}',
                ADAPTER,
                tool,
                mode,
                profile,
                str(self.policy),
                syntax,
                str(len(options) if option_count is None else option_count),
                *options,
                separator,
                *(files if files is not None else [self.source.name]),
            ],
            cwd=self.root,
            capture_output=True,
            input=b'CALLER STDIN MUST NOT BE USED',
            env=os.environ | (environment or {}),
            check=False,
        )
        self.assertEqual(result.returncode == 0, success, result.stderr.decode(errors='replace'))
        if not success:
            self.assertEqual(self.source.read_bytes(), original)
        return result

    def helper(self, body):
        helper = self.root / 'fake-tool'
        helper.write_text(f'#!{sys.executable}\n' + body)
        helper.chmod(0o755)
        return str(helper)

    def test_native_options(self):
        self.invoke(options=['--indent=2', '--preserve-blank-lines'])
        self.assertEqual(self.source.read_text(), '<div>\n  <p>Original</p>\n</div>\n')
        self.source.write_text('<img src="bad.png">\n')
        result = self.invoke(mode='lint', success=False)
        self.assertIn(b'H013', result.stderr)
        self.invoke(mode='lint', options=['--ignore', 'H013'])
        self.invoke(mode='lint', options=['--ignore=H013'])
        self.invoke(profile='jinja')

    def test_option_framing(self):
        outside = self.root / 'outside.txt'
        outside.write_bytes(self.source.read_bytes())
        delimiter = self.root / '--'
        delimiter.write_bytes(self.source.read_bytes())
        original = outside.read_bytes()
        self.invoke(options=['--', outside.name], success=False)
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(delimiter.read_bytes(), original)
        for count in ('-1', '99', 'invalid'):
            with self.subTest(count=count):
                self.invoke(option_count=count, success=False)
        self.invoke(separator='not-a-boundary', success=False)
        self.invoke(files=['--'])
        self.assertEqual(delimiter.read_text(), '<div>\n    <p>Original</p>\n</div>\n')

    def test_invalid_argument_values(self):
        for options in (['--indent'], ['--indent', '--warn'], ['--indent='], ['--preserve-blank-lines=true'], ['-q']):
            with self.subTest(options=options):
                result = self.invoke(options=options, success=False)
                self.assertIn(b'djLint:', result.stderr)
        self.invoke(mode='unknown', success=False)

    def test_policy_types_and_parsers(self):
        for policy in (
            '{',
            '[]',
            '{"indent":true}',
            '{"indent":-1}',
            '{"max_blank_lines":null}',
            '{"format_js":"false"}',
            '{"unknown":true}',
        ):
            with self.subTest(policy=policy):
                self.policy.write_text(policy)
                self.invoke(success=False)
        self.policy.write_text('indent = 2\n')
        self.invoke(syntax='toml')
        self.assertIn('\n  <p>', self.source.read_text())
        self.policy.write_text('[tool.djlint]\nindent = 4\n')
        self.invoke(syntax='pyproject')
        self.assertIn('\n    <p>', self.source.read_text())
        self.policy.write_text('indent = [')
        self.invoke(syntax='toml', success=False)
        self.policy.unlink()
        self.invoke(success=False)

    def test_policy_loading_is_read_only(self):
        adapter = runpy.run_path(ADAPTER)
        self.policy.write_text('{"indent": 2, "preserve_blank_lines": true}')
        before = self.policy.read_bytes()
        parsed = adapter['load_policy'](self.policy, 'json')
        self.assertEqual(parsed, {'indent': 2, 'preserve_blank_lines': True})
        self.assertEqual(self.policy.read_bytes(), before)

    def test_pyproject_requires_a_table(self):
        for content in ('[tool.other]\nindent = 2\n', 'tool = 1\n', '[tool]\ndjlint = false\n'):
            with self.subTest(content=content):
                self.policy.write_text(content)
                result = self.invoke(syntax='pyproject', success=False)
                self.assertIn(b'[tool.djlint]', result.stderr)
                self.assertNotIn(b'Traceback', result.stderr)

    def test_original_filename_and_stdin(self):
        name = "-odd 'comma,braces{[]} café\nname.html.njk"
        target = self.root / name
        target.write_text('<div><p>Original</p></div>\n')
        self.invoke(files=[name])
        self.assertEqual(target.read_text(), '<div>\n    <p>Original</p>\n</div>\n')
        target.write_text('<img src="bad.png">\n')
        self.policy.write_text(json.dumps({'per-file-ignores': {re.escape(name): 'H013'}}))
        self.invoke(mode='lint', files=[name])

    def test_source_boundaries(self):
        target = self.root / 'link.html.njk'
        target.symlink_to(self.source)
        for files in ([target.name], ['missing.html.njk'], ['sub/../input.html.njk']):
            with self.subTest(files=files):
                self.invoke(files=files, success=False)
        with tempfile.TemporaryDirectory() as outside:
            external = pathlib.Path(outside) / 'outside.html.njk'
            external.write_bytes(self.source.read_bytes())
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
            for filename in (str(external), 'linked/outside.html.njk'):
                self.invoke(files=[filename], success=False)
                self.assertEqual(external.read_bytes(), self.source.read_bytes())

    def test_tool_errors_and_diagnostics_preserve_source(self):
        for body in (
            'import sys\nprint("partial output")\nsys.exit(1)\n',
            'import sys\nprint("partial output")\nprint("diagnostic", file=sys.stderr)\n',
            'pass\n',
        ):
            with self.subTest(body=body):
                self.invoke(tool=self.helper(body), success=False)
        self.invoke(tool='/missing/djlint', success=False)
        self.source.write_bytes(b'\xff\xfe\x80')
        self.invoke(success=False)

    def test_empty_input_and_noop_preserve_mtime(self):
        for content in (b'', b'\n', b'<p>unchanged</p>\n'):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                self.invoke()
                before = self.source.stat().st_mtime_ns
                self.invoke()
                self.assertEqual(self.source.stat().st_mtime_ns, before)
        self.invoke(files=[])

    def test_failure_does_not_skip_later_files(self):
        self.source.write_text('<img src="bad.png">\n')
        second = self.root / 'second.html.njk'
        second.write_text('<img src="also-bad.png">\n')
        result = self.invoke(mode='lint', files=[self.source.name, second.name], success=False)
        self.assertIn(self.source.name.encode(), result.stderr)
        self.assertIn(second.name.encode(), result.stderr)
        self.assertEqual(result.stderr.count(b'H013'), 2)


if __name__ == '__main__':
    unittest.main()
