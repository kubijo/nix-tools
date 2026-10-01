"""Audit selections without running formatters, linters, or project tests."""

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile


def command(arguments, **kwargs):
    return subprocess.check_output(arguments, **kwargs)


def paths(arguments, *, discovery=False):
    if discovery:
        result = subprocess.run(arguments, capture_output=True, check=False)
        # fd reports traversal errors without necessarily returning nonzero.
        if result.stderr:
            raise ValueError(f'file discovery failed: {os.fsdecode(result.stderr)}')
        result.check_returncode()
        output = result.stdout
    else:
        output = command(arguments)
    return {os.fsdecode(value).removeprefix('./') for value in output.split(b'\0') if value}


def scope(spec):
    def select(patterns):
        # Git glob pathspecs make these declarations explicit and auditable.
        return (
            paths(
                ['git', 'ls-files', '--cached', '-z', '--']
                + [':(glob)' + (pattern if '/' in pattern else '**/' + pattern) for pattern in patterns]
            )
            if patterns
            else set()
        )

    return select(spec['includes']) - select(spec.get('exclude', []))


def report(config):
    root = pathlib.Path.cwd()
    while not (root / config['treeRootFile']).exists():
        if root == root.parent:
            raise ValueError('cannot find coverage treeRootFile')
        root = root.parent
    os.chdir(root)
    git_root = pathlib.Path(os.fsdecode(command(['git', 'rev-parse', '--show-toplevel'])).strip())
    if git_root != root:
        raise ValueError('coverage treeRootFile must identify the Git worktree root')
    tracked = paths(['git', 'ls-files', '--cached', '-z'])
    untracked = paths(['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    ignored = paths(['git', 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'])
    rows = {
        path: {
            'path': path,
            'in_flake_source': os.path.lexists(pathlib.Path(config['src']) / path),
            'present': pathlib.Path(path).is_file(),
            'format': [],
            'lint': [],
            'project_checks': [],
            'exceptions': [],
        }
        for path in sorted(tracked)
    }
    with tempfile.TemporaryDirectory(prefix='nix-tools-coverage-') as directory:
        env = os.environ | {'NIX_TOOLS_COVERAGE_RECORDS': directory, 'XDG_CACHE_HOME': directory}
        if config['formatKinds']:
            subprocess.run(
                [
                    'treefmt',
                    '--config-file',
                    config['treefmtConfig'],
                    '--tree-root',
                    str(root),
                    '--walk',
                    'git',
                    '--no-cache',
                ],
                check=True,
                env=env,
                stdout=sys.stderr,
            )
        for record in pathlib.Path(directory).glob('*.json'):
            name, files = json.loads(record.read_text())
            for path in files:
                path = os.path.relpath(path, root)
                if path in rows:
                    rows[path]['format'].append({'name': name, 'kind': config['formatKinds'][name]})
    for checker in config['files']:
        selected = set()
        for search in checker['searchPaths'] if checker['discoveryArgs'] else []:
            try:
                mode = os.stat(search).st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode):
                raise ValueError(f'not a discovery directory: {search}')
            for arguments in checker['discoveryArgs']:
                selected |= {os.path.relpath(path, root) for path in paths(['fd', *arguments, search], discovery=True)}
        for path in selected & tracked:
            rows[path]['lint'].append({'name': checker['name'], 'kind': checker['kind']})
    if config['debian']:
        selected = json.loads(
            command(
                [config['debian']['command'], 'coverage'],
                env=os.environ | {'REPOCHK_EXCLUDES_JSON': json.dumps(config['debian']['excludes'])},
            )
        )
        for path in set(selected) & tracked:
            rows[path]['lint'].append({'name': 'debian', 'kind': 'semantic'})
    for name, declaration in config['declarations'].items():
        for path in scope(declaration) & tracked:
            rows[path]['project_checks'].append({'name': name, **declaration, 'scope': 'declared'})
    for exception in config['exceptions']:
        for path in scope(exception) & tracked:
            rows[path]['exceptions'].append({'reason': exception['reason'], 'stages': exception['stages']})
    for row in rows.values():
        waived = {stage for exception in row['exceptions'] for stage in exception['stages']}
        checks = row['lint'] + row['project_checks']
        covered = {
            'format': bool(row['format']),
            'lint': any(check['kind'] in ('semantic', 'syntax') for check in checks),
            'tests': any(check['kind'] == 'test' for check in checks),
        }
        row['gaps'] = [stage for stage in config['required'] if not covered[stage] and stage not in waived]
        if not row['in_flake_source']:
            row['gaps'].append('flake-source')
        if not row['present']:
            row['gaps'].append('missing-file')
        row['format'].sort(key=lambda item: item['name'])
        row['lint'].sort(key=lambda item: item['name'])
    return {
        'version': 1,
        'scope': 'Selection audit; project scope is declared. No check results are implied.',
        'files': list(rows.values()),
        'untracked': [
            {
                'path': path,
                'reason': 'Untracked files are omitted by Git flakes; add intended sources to Git.',
                'in_flake_source': os.path.lexists(pathlib.Path(config['src']) / path),
            }
            for path in sorted(untracked)
        ],
        'ignored': [
            {'path': path, 'reason': 'Ignored by Git; not part of the tracked source census.'}
            for path in sorted(ignored)
        ],
        'unmapped_project_checks': sorted(
            set(config['projectNames']) - set(config['declarations']) - ({'lint:debian'} if config['debian'] else set())
        ),
    }


def main():
    if sys.argv[1] == '--record':
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', dir=os.environ['NIX_TOOLS_COVERAGE_RECORDS'], delete=False
        ) as output:
            json.dump([sys.argv[2], sys.argv[3:]], output)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    parser.add_argument('--json', action='store_true', help='emit the complete machine-readable audit')
    parser.add_argument(
        '--check', action='store_true', help='fail on gaps, untracked files, or unmapped project checks'
    )
    args = parser.parse_args()
    result = report(json.loads(pathlib.Path(args.config).read_text()))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['scope'])
        for row in result['files']:
            details = [
                f'{phase}=' + ','.join(f'{item["name"]}({item["kind"]})' for item in row[phase])
                for phase in ('format', 'lint', 'project_checks')
            ]
            details += ['exception=' + item['reason'] for item in row['exceptions']]
            print(f'{row["path"]!r}: {"; ".join(details)}; gaps={",".join(row["gaps"]) or "none"}')
        for kind in ('untracked', 'ignored'):
            for item in result[kind]:
                print(f'{kind}: {item["path"]!r}: {item["reason"]}')
        for name in result['unmapped_project_checks']:
            print(f'unmapped project check: {name}')
    return int(
        args.check
        and (
            any(row['gaps'] for row in result['files'])
            or bool(result['untracked'])
            or bool(result['unmapped_project_checks'])
        )
    )


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'repo-coverage: {error}', file=sys.stderr)
        sys.exit(1)
