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
from collections.abc import Callable, Sequence
from typing import cast, override

ADAPTER = sys.argv.pop(1)
TOOL = sys.argv.pop(1)


class Adapter(unittest.TestCase):
    directory: tempfile.TemporaryDirectory[str]
    root: pathlib.Path
    source: pathlib.Path
    policy: pathlib.Path

    def __init__(self, methodName: str = 'runTest') -> None:
        super().__init__(methodName)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.source = self.root / 'input.html.njk'
        self.policy = self.root / 'policy.json'

    @override
    def setUp(self) -> None:
        _ = self.source.write_text('<div><p>Original</p></div>\n')
        _ = self.policy.write_text('{}')

    def invoke(
        self,
        mode: str = 'format',
        options: Sequence[str] = (),
        files: Sequence[str] | None = None,
        syntax: str = 'json',
        tool: str = TOOL,
        success: bool = True,
        environment: dict[str, str] | None = None,
        profile: str = 'nunjucks',
        option_count: str | None = None,
        separator: str = '--',
    ) -> subprocess.CompletedProcess[bytes]:
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

    def helper(self, body: str) -> str:
        helper = self.root / 'fake-tool'
        _ = helper.write_text(f'#!{sys.executable}\n' + body)
        helper.chmod(0o755)
        return str(helper)

    def test_native_options(self) -> None:
        _ = self.invoke(options=['--indent=2', '--preserve-blank-lines'])
        self.assertEqual(self.source.read_text(), '<div>\n  <p>Original</p>\n</div>\n')
        _ = self.source.write_text('<img src="bad.png">\n')
        result = self.invoke(mode='lint', success=False)
        self.assertIn(b'H013', result.stderr)
        _ = self.invoke(mode='lint', options=['--ignore', 'H013'])
        _ = self.invoke(mode='lint', options=['--ignore=H013'])
        _ = self.invoke(profile='jinja')

    def test_option_framing(self) -> None:
        outside = self.root / 'outside.txt'
        _ = outside.write_bytes(self.source.read_bytes())
        delimiter = self.root / '--'
        _ = delimiter.write_bytes(self.source.read_bytes())
        original = outside.read_bytes()
        _ = self.invoke(options=['--', outside.name], success=False)
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(delimiter.read_bytes(), original)
        for count in ('-1', '99', 'invalid'):
            with self.subTest(count=count):
                _ = self.invoke(option_count=count, success=False)
        _ = self.invoke(separator='not-a-boundary', success=False)
        _ = self.invoke(files=['--'])
        self.assertEqual(delimiter.read_text(), '<div>\n    <p>Original</p>\n</div>\n')

    def test_invalid_argument_values(self) -> None:
        for options in (['--indent'], ['--indent', '--warn'], ['--indent='], ['--preserve-blank-lines=true'], ['-q']):
            with self.subTest(options=options):
                result = self.invoke(options=options, success=False)
                self.assertIn(b'djLint:', result.stderr)
        _ = self.invoke(mode='unknown', success=False)

    def test_policy_types_and_parsers(self) -> None:
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
                _ = self.policy.write_text(policy)
                _ = self.invoke(success=False)
        _ = self.policy.write_text('indent = 2\n')
        _ = self.invoke(syntax='toml')
        self.assertIn('\n  <p>', self.source.read_text())
        _ = self.policy.write_text('[tool.djlint]\nindent = 4\n')
        _ = self.invoke(syntax='pyproject')
        self.assertIn('\n    <p>', self.source.read_text())
        _ = self.policy.write_text('indent = [')
        _ = self.invoke(syntax='toml', success=False)
        self.policy.unlink()
        _ = self.invoke(success=False)

    def test_policy_loading_is_read_only(self) -> None:
        adapter = cast(dict[str, object], runpy.run_path(ADAPTER))
        load_policy = cast(Callable[[pathlib.Path, str], dict[str, object]], adapter['load_policy'])
        _ = self.policy.write_text('{"indent": 2, "preserve_blank_lines": true}')
        before = self.policy.read_bytes()
        parsed = load_policy(self.policy, 'json')
        self.assertEqual(parsed, {'indent': 2, 'preserve_blank_lines': True})
        self.assertEqual(self.policy.read_bytes(), before)

    def test_pyproject_requires_a_table(self) -> None:
        for content in ('[tool.other]\nindent = 2\n', 'tool = 1\n', '[tool]\ndjlint = false\n'):
            with self.subTest(content=content):
                _ = self.policy.write_text(content)
                result = self.invoke(syntax='pyproject', success=False)
                self.assertIn(b'[tool.djlint]', result.stderr)
                self.assertNotIn(b'Traceback', result.stderr)

    def test_original_filename_and_stdin(self) -> None:
        name = "-odd 'comma,braces{[]} café\nname.html.njk"
        target = self.root / name
        _ = target.write_text('<div><p>Original</p></div>\n')
        _ = self.invoke(files=[name])
        self.assertEqual(target.read_text(), '<div>\n    <p>Original</p>\n</div>\n')
        _ = target.write_text('<img src="bad.png">\n')
        _ = self.policy.write_text(json.dumps({'per-file-ignores': {re.escape(name): 'H013'}}))
        _ = self.invoke(mode='lint', files=[name])

    def test_source_boundaries(self) -> None:
        target = self.root / 'link.html.njk'
        target.symlink_to(self.source)
        for files in ([target.name], ['missing.html.njk'], ['sub/../input.html.njk']):
            with self.subTest(files=files):
                _ = self.invoke(files=files, success=False)
        with tempfile.TemporaryDirectory() as outside:
            external = pathlib.Path(outside) / 'outside.html.njk'
            _ = external.write_bytes(self.source.read_bytes())
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
            for filename in (str(external), 'linked/outside.html.njk'):
                _ = self.invoke(files=[filename], success=False)
                self.assertEqual(external.read_bytes(), self.source.read_bytes())

    def test_tool_errors_and_diagnostics_preserve_source(self) -> None:
        for body in (
            'import sys\nprint("partial output")\nsys.exit(1)\n',
            'import sys\nprint("partial output")\nprint("diagnostic", file=sys.stderr)\n',
            'pass\n',
        ):
            with self.subTest(body=body):
                _ = self.invoke(tool=self.helper(body), success=False)
        _ = self.invoke(tool='/missing/djlint', success=False)
        _ = self.source.write_bytes(b'\xff\xfe\x80')
        _ = self.invoke(success=False)

    def test_empty_input_and_noop_preserve_mtime(self) -> None:
        for content in (b'', b'\n', b'<p>unchanged</p>\n'):
            with self.subTest(content=content):
                _ = self.source.write_bytes(content)
                _ = self.invoke()
                before = self.source.stat().st_mtime_ns
                _ = self.invoke()
                self.assertEqual(self.source.stat().st_mtime_ns, before)
        _ = self.invoke(files=[])

    def test_failure_does_not_skip_later_files(self) -> None:
        _ = self.source.write_text('<img src="bad.png">\n')
        second = self.root / 'second.html.njk'
        _ = second.write_text('<img src="also-bad.png">\n')
        result = self.invoke(mode='lint', files=[self.source.name, second.name], success=False)
        self.assertIn(self.source.name.encode(), result.stderr)
        self.assertIn(second.name.encode(), result.stderr)
        self.assertEqual(result.stderr.count(b'H013'), 2)


if __name__ == '__main__':
    _ = unittest.main()
