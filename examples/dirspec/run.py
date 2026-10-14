"""Show directory checks against a fresh sample tree for each scenario."""

import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import tyro
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
import dirspec

HERE = Path(__file__).resolve().parent
type Case = Literal[
    'passing', 'extras', 'allow-extras', 'permissions', 'missing', 'forbidden', 'counts', 'dangling', 'size'
]


@dataclass(frozen=True)
class Scenario:
    description: str
    expected: int


SCENARIOS: dict[Case, Scenario] = {
    'passing': Scenario('Declared paths, permissions, sizes, symlink, and exclusions.', 0),
    'extras': Scenario('An undeclared file fails strict checking.', 1),
    'allow-extras': Scenario('--no-strict permits the same undeclared file.', 0),
    'permissions': Scenario('The application lacks owner execute permission.', 1),
    'missing': Scenario('The required README is missing.', 1),
    'forbidden': Scenario('A source map violates count = 0.', 1),
    'counts': Scenario('Three JSON files exceed max = 2.', 1),
    'dangling': Scenario('The symlink points to a missing, unexpected target.', 1),
    'size': Scenario('An empty README violates min_size = 1.', 1),
}


def run_example(case: Case, number: int, total: int) -> bool:
    scenario = SCENARIOS[case]
    console = dirspec.output_console()
    console.print()
    console.rule(Text(f'Example {number}/{total}: {case}', style='bold'), style='dim', align='left')
    console.print(Text(scenario.description))
    outcome = 'pass' if scenario.expected == 0 else 'fail'
    console.print(Text(f'Expected: {outcome} (checker exit {scenario.expected}).', style='dim'))
    console.print()
    with tempfile.TemporaryDirectory(prefix='dirspec-example-') as temporary:
        tree = Path(temporary) / 'tree'
        _ = shutil.copytree(HERE / 'tree', tree, symlinks=True)
        # Normalize permissions copied from a checkout or the read-only Nix store.
        for path in (tree, *tree.rglob('*')):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
        (tree / 'bin/app').chmod(0o755)
        if case in {'extras', 'allow-extras'}:
            _ = (tree / 'extra.txt').write_text('Undeclared output.\n')
        elif case == 'permissions':
            (tree / 'bin/app').chmod(0o644)
        elif case == 'missing':
            (tree / 'README.txt').unlink()
        elif case == 'forbidden':
            _ = (tree / 'share/app.map').write_text('{}\n')
        elif case == 'counts':
            for name in ('second.json', 'third.json'):
                _ = (tree / 'share' / name).write_text('{}\n')
        elif case == 'dangling':
            (tree / 'current').unlink()
            (tree / 'current').symlink_to('bin/missing')
        elif case == 'size':
            _ = (tree / 'README.txt').write_text('')

        arguments = [str(tree), '--spec', str(HERE / 'prescription.toml')]
        if case == 'allow-extras':
            arguments.append('--no-strict')
        actual = dirspec.main(arguments)
    console.print(
        Text(
            f'Checker exit: {actual} (expected {scenario.expected}).',
            style='dim' if actual == scenario.expected else 'bold red',
        )
    )
    console.print()
    return actual == scenario.expected


@dataclass
class Options:
    case: tyro.conf.Positional[Case | Literal['all']] = 'all'
    """Scenario to show, or all scenarios."""


def main(argv: Sequence[str] | None = None) -> int:
    options = cast(Callable[..., Options], tyro.cli)(Options, prog='dirspec-examples', description=__doc__, args=argv)
    selected = tuple(SCENARIOS) if options.case == 'all' else (options.case,)
    results = [run_example(case, number, len(selected)) for number, case in enumerate(selected, start=1)]
    return int(not all(results))


if __name__ == '__main__':
    sys.exit(main())
