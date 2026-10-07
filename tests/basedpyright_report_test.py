"""Reporter output and pipeline status tests."""

import contextlib
import io
import json
import os
import pathlib
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from typing import cast, override
from unittest.mock import patch

from rich.console import Console
from rich.text import Text

REPORTER = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(REPORTER.parent))
from terminal_env import AGENT_ENVS

MODULE = cast(dict[str, object], runpy.run_path(str(REPORTER)))
render = cast(Callable[[dict[str, object], pathlib.Path], None], MODULE['render'])
ROOT = pathlib.Path('/project')
REPORT: dict[str, object] = {
    'generalDiagnostics': [
        {
            'file': '/project/src/app.py',
            'severity': 'error',
            'message': 'Wrong type [literal]\nExpected int',
            'range': {'start': {'line': 6, 'character': 2}},
            'rule': 'reportArgumentType',
        },
        {'file': '/project/src/app.py', 'severity': 'warning', 'message': 'Check this'},
        {'file': '/shared/helper.py', 'severity': 'information', 'message': 'A note'},
    ],
    'summary': {'errorCount': 1, 'warningCount': 1, 'informationCount': 1},
}


class Terminal(io.StringIO):
    @override
    def isatty(self) -> bool:
        return True


class Renderer(unittest.TestCase):
    def output(self, env: dict[str, str], tty: bool = False) -> str:
        stream = Terminal() if tty else io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(stream):
            render(REPORT, ROOT)
        return stream.getvalue()

    def test_plain_relative_paths_and_complete_messages(self) -> None:
        output = self.output({})
        self.assertIn('src/app.py:7:3: error: Wrong type [literal] Expected int [reportArgumentType]', output)
        self.assertIn('../shared/helper.py: information: A note', output)
        self.assertIn('1 errors, 1 warnings, 1 notes', output)
        self.assertNotIn('\x1b[', output)

    def test_tty_clanker_and_force(self) -> None:
        self.assertIn('\x1b[', self.output({}, tty=True))
        self.assertEqual(self.output({}, tty=True).count('src/app.py'), 2)
        for marker in AGENT_ENVS:
            with self.subTest(marker=marker):
                output = self.output({marker: '1'}, tty=True)
                self.assertNotIn('\x1b[', output)
                self.assertEqual(output.count('src/app.py'), 2)
        for flag in ('FORCE_COLOR', 'CLICOLOR_FORCE'):
            with self.subTest(flag=flag):
                self.assertIn('\x1b[', self.output({flag: '1', 'NO_COLOR': '1', 'IN_CLANKER': '1'}))
                self.assertNotIn('\x1b[', self.output({flag: '0', 'IN_CLANKER': '1'}, tty=True))
        self.assertNotIn('\x1b[', self.output({'NO_COLOR': '1'}, tty=True))

    def test_pretty_locations_spacing_and_wrapping(self) -> None:
        output = Text.from_ansi(self.output({}, tty=True)).plain
        lines = [line.rstrip() for line in output.splitlines()]
        self.assertEqual(lines[0], 'src/app.py:7:3')
        self.assertEqual(lines[1], '  error: Wrong type [literal] [reportArgumentType]')
        self.assertEqual(lines[2], '    Expected int')
        self.assertEqual(lines[3:6], ['', 'src/app.py', '  warning: Check this'])
        narrow = Text.from_ansi(self.output({'COLUMNS': '20'}, tty=True)).plain
        for block in narrow.split('\n\n')[:-1]:
            header, *body = block.splitlines()
            self.assertIn(header, ('src/app.py:7:3', 'src/app.py', '../shared/helper.py'))
            for line in body:
                self.assertTrue(line.startswith('  '))
                self.assertLessEqual(len(line), 20)

    def test_hyperlinks_and_plain_fallback(self) -> None:
        output = self.output({'TERM_PROGRAM': 'zed'}, tty=True)
        targets = re.findall(r'\x1b\]8;id=[^;]*;([^\x1b]+)\x1b\\', output)
        self.assertEqual(
            targets,
            ['zed://file/project/src/app.py:7:3', 'zed://file/project/src/app.py', 'zed://file/shared/helper.py'],
        )
        for env, tty in (
            ({}, True),
            ({'TERM_PROGRAM': 'zed'}, False),
            ({'TERM_PROGRAM': 'zed', 'IN_CLANKER': '1'}, True),
            ({'TERM_PROGRAM': 'zed', 'NO_COLOR': '1'}, True),
            ({'TERM_PROGRAM': 'zed', 'FORCE_HYPERLINK': '0'}, True),
            ({'TERM': 'dumb'}, True),
        ):
            with self.subTest(env=env, tty=tty):
                self.assertNotIn('\x1b]8;', self.output(env, tty=tty))
        self.assertIn(';file:///project/src/app.py\x1b\\', self.output({'FORCE_HYPERLINK': '1'}, tty=True))
        diagnostic = {'file': '/project/src/a b#c.py', 'severity': 'error', 'message': 'Example'}
        with patch.dict(REPORT, {'generalDiagnostics': [diagnostic]}):
            output = self.output({'TERM_PROGRAM': 'zed'}, tty=True)
        self.assertIn(';zed://file/project/src/a%20b%23c.py\x1b\\', output)
        self.assertIn('src/a b#c.py\n  error: Example', Text.from_ansi(output).plain)

    def test_styles_do_not_spill_past_location(self) -> None:
        output = Text.from_ansi(self.output({'TERM_PROGRAM': 'zed'}, tty=True))
        console = Console()
        for fragment in ('Wrong type', '[reportArgumentType]', 'Expected int'):
            style = output.get_style_at_offset(console, output.plain.index(fragment))
            self.assertIsNone(style.color)
            self.assertFalse(style.bold)
            self.assertIsNone(style.link)
        for fragment in ('[reportArgumentType]', 'Expected int'):
            self.assertTrue(output.get_style_at_offset(console, output.plain.index(fragment)).dim)

    def test_pipeline_status_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            producer = pathlib.Path(directory) / 'producer.py'
            for status in (0, 1, 23):
                _ = producer.write_text(f'import sys\nprint({json.dumps(REPORT)!r})\nsys.exit({status})\n')
                result = subprocess.run(
                    [
                        'bash',
                        '-o',
                        'pipefail',
                        '-c',
                        '"$1" "$2" | "$1" "$3" "$4"',
                        'bash',
                        sys.executable,
                        str(producer),
                        str(REPORTER),
                        str(ROOT),
                    ],
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, status, result.stderr)
        for document in ('not json', '{}'):
            result = subprocess.run(
                [sys.executable, str(REPORTER), str(ROOT)],
                input=document,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('basedpyright-report:', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    _ = unittest.main()
