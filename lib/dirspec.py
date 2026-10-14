"""Validate a directory tree against a TOML prescription."""

import errno
import grp
import os
import pwd
import stat
import sys
import tomllib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Annotated, cast

import tyro
from rich.console import Console
from rich.text import Text
from rich.tree import Tree
from terminal_env import is_color_forced, should_print_pretty
from wcmatch import glob

GLOB_FLAGS = glob.GLOBSTAR | glob.BRACE | glob.DOTMATCH | glob.FORCEUNIX
RULE_FIELDS = {
    'type',
    'count',
    'min',
    'max',
    'mode',
    'mode_required',
    'mode_forbidden',
    'owner',
    'group',
    'size',
    'min_size',
    'max_size',
    'link_target',
    'link_exists',
}


class SpecError(ValueError):
    """Invalid prescription."""


@dataclass(frozen=True)
class Bounds:
    minimum: int
    maximum: int | None = None

    def accepts(self, actual: int) -> bool:
        return actual >= self.minimum and (self.maximum is None or actual <= self.maximum)

    def describe(self) -> str:
        if self.minimum == self.maximum:
            return f'exactly {self.minimum}'
        if self.maximum is None:
            return f'at least {self.minimum}'
        return f'{self.minimum}..{self.maximum}'


@dataclass(frozen=True)
class Rule:
    pattern: str
    matcher: glob.WcMatcher[str]
    count: Bounds
    kind: str = 'any'
    mode: int | None = None
    mode_required: int = 0
    mode_forbidden: int = 0
    owner: int | None = None
    group: int | None = None
    size: Bounds | None = None
    link_target: str | None = None
    link_exists: bool | None = None


@dataclass(frozen=True)
class Spec:
    rules: tuple[Rule, ...]
    strict: bool = False
    exclude: tuple[glob.WcMatcher[str], ...] = ()


@dataclass(frozen=True)
class Entry:
    path: Path
    info: os.stat_result
    kind: str
    link_target: str | None = None

    @cached_property
    def resolves(self) -> bool:
        return target_exists(self.path)


@dataclass(frozen=True)
class Problem:
    path: str | None
    pattern: str | None
    detail: str


@dataclass
class Report:
    root: Path
    entries: dict[str, Entry]
    rule_count: int
    declared: set[str] = field(default_factory=set)
    parents: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    problems: list[Problem] = field(default_factory=list)
    extras: set[str] = field(default_factory=set)


def table(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SpecError(f'{location}: expected a table')
    return cast(dict[str, object], value)


def integer(values: dict[str, object], key: str, location: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SpecError(f'{location}.{key}: expected a nonnegative integer')
    return value


def bounds(values: dict[str, object], keys: tuple[str, str, str], default: int, location: str) -> Bounds:
    exact_key, low_key, high_key = keys
    exact = integer(values, exact_key, location)
    low = integer(values, low_key, location)
    high = integer(values, high_key, location)
    if exact is not None:
        if low is not None or high is not None:
            raise SpecError(f'{location}: {exact_key} cannot accompany {low_key}/{high_key}')
        return Bounds(exact, exact)
    low = default if low is None else low
    if high is not None and low > high:
        raise SpecError(f'{location}: {low_key} exceeds {high_key}')
    return Bounds(low, high)


def compile_pattern(value: object, location: str) -> tuple[str, glob.WcMatcher[str]]:
    if not isinstance(value, str) or not value or '\x00' in value:
        raise SpecError(f'{location}: expected a nonempty path pattern')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts:
        raise SpecError(f'{location}: patterns must be relative to the target, without .. components')
    pattern = path.as_posix()
    try:
        return pattern, glob.compile(pattern, flags=GLOB_FLAGS)
    except Exception as error:
        # wcmatch's expansion-limit exception has no public import.
        raise SpecError(f'{location}: {error}') from error


def identity(values: dict[str, object], key: str, location: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if not isinstance(value, str) or not value:
        raise SpecError(f'{location}.{key}: expected an account name or nonnegative numeric ID')
    try:
        return pwd.getpwnam(value).pw_uid if key == 'owner' else grp.getgrnam(value).gr_gid
    except (KeyError, ValueError) as error:
        raise SpecError(f'{location}.{key}: unknown account {value!r}') from error


def parse_rule(pattern: str, raw: object) -> Rule:
    location = f'paths.{pattern!r}'
    values = table(raw, location)
    unknown = values.keys() - RULE_FIELDS
    if unknown:
        raise SpecError(f'{location}: unknown fields: {", ".join(sorted(unknown))}')
    pattern, matcher = compile_pattern(pattern, location)
    kind = values.get('type', 'any')
    if not isinstance(kind, str) or kind not in {'any', 'file', 'directory', 'symlink'}:
        raise SpecError(f'{location}.type: expected any, file, directory, or symlink')
    mode = integer(values, 'mode', location)
    required = integer(values, 'mode_required', location) or 0
    forbidden = integer(values, 'mode_forbidden', location) or 0
    if any(value > 0o7777 for value in (mode or 0, required, forbidden)):
        raise SpecError(f'{location}: permission values must be between 0o0000 and 0o7777')
    if required & forbidden or (mode is not None and (mode & required != required or mode & forbidden)):
        raise SpecError(f'{location}: contradictory permission assertions')
    link_target = values.get('link_target')
    if link_target is not None and (not isinstance(link_target, str) or not link_target or '\x00' in link_target):
        raise SpecError(f'{location}.link_target: expected a nonempty target string')
    link_exists = values.get('link_exists')
    if link_exists is not None and not isinstance(link_exists, bool):
        raise SpecError(f'{location}.link_exists: expected a boolean')
    return Rule(
        pattern=pattern,
        matcher=matcher,
        kind=kind,
        count=bounds(values, ('count', 'min', 'max'), 1, location),
        mode=mode,
        mode_required=required,
        mode_forbidden=forbidden,
        owner=identity(values, 'owner', location),
        group=identity(values, 'group', location),
        size=bounds(values, ('size', 'min_size', 'max_size'), 0, location)
        if {'size', 'min_size', 'max_size'} & values.keys()
        else None,
        link_target=link_target,
        link_exists=link_exists,
    )


def parse_spec(document: dict[str, object]) -> Spec:
    unknown = document.keys() - {'version', 'strict', 'exclude', 'paths'}
    if unknown:
        raise SpecError(f'unknown prescription fields: {", ".join(sorted(unknown))}')
    version = document.get('version')
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise SpecError('version: expected 1')
    strict = document.get('strict', False)
    if not isinstance(strict, bool):
        raise SpecError('strict: expected a boolean')
    paths = table(document.get('paths'), 'paths')
    if not paths:
        raise SpecError('paths: expected at least one rule')
    raw_exclude = document.get('exclude', [])
    if not isinstance(raw_exclude, list):
        raise SpecError('exclude: expected an array of path patterns')
    exclude = cast(list[object], raw_exclude)
    excluded = tuple(compile_pattern(value, 'exclude') for value in exclude)
    if any(pattern == '.' for pattern, _ in excluded):
        raise SpecError('exclude: the target root cannot be excluded')
    return Spec(
        rules=tuple(parse_rule(pattern, raw) for pattern, raw in paths.items()),
        strict=strict,
        exclude=tuple(matcher for _, matcher in excluded),
    )


def load_spec(path: Path) -> Spec:
    with path.open('rb') as stream:
        document = cast(dict[str, object], cast(object, tomllib.load(stream)))
    return parse_spec(document)


def entry_kind(info: os.stat_result) -> str:
    if stat.S_ISDIR(info.st_mode):
        return 'directory'
    if stat.S_ISREG(info.st_mode):
        return 'file'
    if stat.S_ISLNK(info.st_mode):
        return 'symlink'
    return 'special'


def walk(root: Path, exclude: tuple[glob.WcMatcher[str], ...]) -> dict[str, Entry]:
    def directory_names(descriptor: int) -> Iterator[str]:
        with os.scandir(descriptor) as scanned:
            return iter(sorted(child.name for child in scanned))

    flags = os.O_RDONLY | os.O_DIRECTORY
    descriptor = os.open(root, flags)
    # Register each descriptor before any operation that can fail. Only ancestors stay open.
    pending: list[tuple[str, int, Iterator[str]]] = [('.', descriptor, iter(()))]
    try:
        entries = {'.': Entry(root, os.fstat(descriptor), 'directory')}

        def unchanged(name: str, current: os.stat_result) -> None:
            if not os.path.samestat(entries[name].info, current):
                raise OSError(errno.ESTALE, 'directory changed during traversal', str(root / name))

        pending[0] = ('.', descriptor, directory_names(descriptor))
        while pending:
            parent, directory, children = pending[-1]
            child = next(children, None)
            if child is None:
                current = (
                    root.stat()
                    if parent == '.'
                    else os.stat(PurePosixPath(parent).name, dir_fd=pending[-2][1], follow_symlinks=False)
                )
                unchanged(parent, current)
                os.close(directory)
                _ = pending.pop()
                continue
            name = child if parent == '.' else f'{parent}/{child}'
            if any(matcher.match(name) for matcher in exclude):
                continue
            child_info = os.stat(child, dir_fd=directory, follow_symlinks=False)
            kind = entry_kind(child_info)
            path = root / name
            target = os.readlink(child, dir_fd=directory) if kind == 'symlink' else None
            entries[name] = Entry(path, child_info, kind, target)
            if kind == 'directory':
                child_descriptor = os.open(child, flags | os.O_NOFOLLOW, dir_fd=directory)
                pending.append((name, child_descriptor, iter(())))
                unchanged(name, os.fstat(child_descriptor))
                pending[-1] = (name, child_descriptor, directory_names(child_descriptor))
        return entries
    finally:
        while pending:
            os.close(pending.pop()[1])


def target_exists(path: Path) -> bool:
    try:
        _ = path.stat()
        return True
    except OSError as error:
        if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            return False
        raise


def assertions(rule: Rule, entry: Entry) -> list[str]:
    failures: list[str] = []
    if rule.kind != 'any' and entry.kind != rule.kind:
        failures.append(f'type: expected {rule.kind}, got {entry.kind}')
    if rule.mode is not None or rule.mode_required or rule.mode_forbidden:
        mode = stat.S_IMODE(entry.info.st_mode)
        if entry.kind == 'symlink':
            failures.append('mode: permission assertions are unsupported for symlinks')
        else:
            if rule.mode is not None and mode != rule.mode:
                failures.append(f'mode: expected 0o{rule.mode:04o}, got 0o{mode:04o}')
            if mode & rule.mode_required != rule.mode_required:
                failures.append(f'mode_required: expected bits 0o{rule.mode_required:04o}, got 0o{mode:04o}')
            if mode & rule.mode_forbidden:
                failures.append(f'mode_forbidden: prohibited bits 0o{rule.mode_forbidden:04o}, got 0o{mode:04o}')
    for key, expected, actual in (('owner', rule.owner, entry.info.st_uid), ('group', rule.group, entry.info.st_gid)):
        if expected is not None and expected != actual:
            failures.append(f'{key}: expected {expected}, got {actual}')
    if rule.size is not None:
        if entry.kind != 'file':
            failures.append(f'size: expected a regular file, got {entry.kind}')
        elif not rule.size.accepts(entry.info.st_size):
            failures.append(f'size: expected {rule.size.describe()} bytes, got {entry.info.st_size}')
    if rule.link_target is not None or rule.link_exists is not None:
        if entry.kind != 'symlink':
            failures.append(f'link: expected a symlink, got {entry.kind}')
        else:
            if rule.link_target is not None and entry.link_target != rule.link_target:
                failures.append(f'link_target: expected {rule.link_target!r}, got {entry.link_target!r}')
            if rule.link_exists is not None:
                exists = entry.resolves
                if exists != rule.link_exists:
                    failures.append(f'link_exists: expected {rule.link_exists}, got {exists}')
    return failures


def check_tree(root: Path, spec: Spec, strict: bool | None = None) -> Report:
    root = root.absolute()
    entries = walk(root, spec.exclude)
    report = Report(root, entries, len(spec.rules))
    for rule in spec.rules:
        matches = [name for name in entries if (name != '.' or rule.pattern == '.') and rule.matcher.match(name)]
        report.declared.update(matches)
        if not rule.count.accepts(len(matches)):
            report.problems.append(
                Problem(None, rule.pattern, f'count: expected {rule.count.describe()}, got {len(matches)}')
            )
            report.failed.update(matches)
        for name in matches:
            report.parents.update(str(parent) for parent in PurePosixPath(name).parents)
            for failure in assertions(rule, entries[name]):
                report.problems.append(Problem(name, rule.pattern, failure))
                report.failed.add(name)
    report.extras = entries.keys() - report.declared - report.parents - {'.'}
    strict_enabled = spec.strict if strict is None else strict
    if strict_enabled:
        for name in sorted(report.extras):
            report.problems.append(Problem(name, None, 'undeclared entry (strict mode)'))
            report.failed.add(name)
    return report


def display(value: str) -> str:
    return ascii(value) if any(ord(char) < 32 or ord(char) == 127 for char in value) else value


def output_console() -> Console:
    forced = is_color_forced()
    colored = (
        should_print_pretty()
        and ('NO_COLOR' not in os.environ or forced)
        and (os.environ.get('CI', '').lower() != 'true' or forced)
    )
    return Console(
        force_terminal=colored, color_system='standard' if colored else None, no_color=not colored, highlight=False
    )


def render(report: Report) -> None:
    console = output_console()
    failed = report.failed.copy()
    for name in report.failed:
        failed.update(str(parent) for parent in PurePosixPath(name).parents)
    if report.problems:
        failed.add('.')

    def label(name: str, *, relative: bool = False) -> Text:
        entry = report.entries[name]
        if name in failed:
            marker, style = '✗', 'red' if name in report.failed else 'dim red'
        elif name in report.extras:
            marker, style = '+', 'dim yellow'
        else:
            marker, style = '✓', 'dim green'
        title = display(str(report.root) if name == '.' else (name if relative else PurePosixPath(name).name))
        if entry.kind == 'directory' and not title.endswith('/'):
            title += '/'
        text = Text.assemble((marker, style), ' ', (title, 'bold' if name in report.failed else 'dim'))
        if entry.kind == 'symlink':
            _ = text.append(f' -> {display(entry.link_target or "")}', style='dim')
        return text

    depth = max(len(PurePosixPath(name).parts) for name in report.entries)
    if depth * 4 + 2 >= console.width:
        # Rich drops nodes when their guides consume the output width.
        console.print(label('.'), soft_wrap=True)
        for name in sorted(report.entries):
            if name != '.':
                console.print(label(name, relative=True), soft_wrap=True)
    else:
        tree = Tree(label('.'), guide_style='dim')
        nodes: dict[str, Tree] = {'.': tree}
        for name in sorted(report.entries):
            if name != '.':
                parent = str(PurePosixPath(name).parent)
                nodes[name] = nodes[parent].add(label(name))
        console.print(tree)
    groups: dict[tuple[str | None, str | None], list[str]] = {}
    for problem in report.problems:
        groups.setdefault((problem.path, problem.pattern), []).append(problem.detail)
    if groups:
        console.print()
        console.print(Text('Failures', style='bold'))
        for index, ((path, pattern), details) in enumerate(groups.items()):
            if index:
                console.print()
            where = display(path) if path is not None else f'pattern {pattern!r}'
            console.print(Text(f'  {where}', style='bold'), soft_wrap=True)
            for detail in details:
                console.print(Text(f'    {detail}'), soft_wrap=True)
            if path is not None and pattern is not None:
                console.print(Text(f'    rule: {pattern!r}', style='dim'), soft_wrap=True)
    failures = len(report.problems)
    summary = (
        f'{len(report.entries) - 1} paths, {report.rule_count} rules, '
        f'{failures} {"failure" if failures == 1 else "failures"}, {len(report.extras)} undeclared'
    )
    status, style = ('FAIL', 'red') if report.problems else ('PASS', 'green')
    console.print()
    console.print(Text.assemble((status, style), '  ', (summary, 'dim')), soft_wrap=True)


@dataclass
class Arguments:
    target: Annotated[Path, tyro.conf.Positional, tyro.conf.arg(metavar='TARGET')]
    """Directory to check."""
    spec: Annotated[Path, tyro.conf.arg(metavar='FILE')]
    """TOML prescription."""
    strict: tyro.conf.DisallowNone[bool | None] = None
    """Override strict checking."""


def main(argv: Sequence[str] | None = None) -> int:
    args = cast(Callable[..., Arguments], tyro.cli)(Arguments, prog='dirspec', description=__doc__, args=argv)
    try:
        report = check_tree(args.target, load_spec(args.spec), args.strict)
        render(report)
        return 1 if report.problems else 0
    except (OSError, ValueError) as error:
        print(f'dirspec: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
