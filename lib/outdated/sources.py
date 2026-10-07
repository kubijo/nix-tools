"""Release entries, Nix input graphs and GitHub workflow references."""

import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Required, TypedDict, cast
from urllib.parse import quote

import yaml
from common import (
    Failure,
    Result,
    UnreadableSource,
    command_json,
    compare,
    relative,
    run,
    snapshot,
    validate_adapter,
    version,
)
from network import Client
from releases import ReleaseSource


class ReleaseEntry(ReleaseSource, total=False):
    name: Required[str]
    version: str
    source: str
    unknown: str
    skip: str
    versionCommand: list[str]
    versionPattern: str
    reporter: str


class NixNode(TypedDict, total=False):
    inputs: dict[str, object]
    locked: dict[str, str]
    original: dict[str, str]


class NixLock(TypedDict):
    nodes: dict[str, NixNode]
    root: str


class NixMetadata(TypedDict):
    locks: NixLock


class WorkflowJob(TypedDict, total=False):
    uses: object
    steps: list[dict[str, object]]


type SourceJob = tuple[str, Callable[[], Result]]


def github_url(repo: str, kind: str, value: str) -> str:
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        return ''
    return f'https://github.com/{repo}/{kind}/{quote(value, safe="")}'


def registry_url(kind: str, project: object, value: str) -> str:
    if not isinstance(project, str) or not project or not value:
        return ''
    name, release = quote(project, safe='@/'), quote(value, safe='')
    if kind == 'pypi':
        return f'https://pypi.org/project/{name}/{release}/'
    if kind == 'npm':
        return f'https://www.npmjs.com/package/{name}/v/{release}'
    if kind == 'crates':
        return f'https://crates.io/crates/{name}/{release}'
    return ''


def release_entry(client: Client, item: ReleaseEntry, provider: str = 'tools', root: Path | None = None) -> Result:
    name, current = item['name'], item.get('version', '')
    source = item.get('source', name)

    if unknown := item.get('unknown'):
        return Result(provider, name, source, 'unknown', current, detail=unknown)

    if skip := item.get('skip'):
        return Result(provider, name, source, 'skipped', current, detail=skip)

    if 'versionCommand' in item:
        if root is None:
            raise Failure('Runtime version discovery requires a repository root')
        with snapshot(root) as work:
            output, _ = run(item['versionCommand'], work, client.timeout)
        current = output.strip()
        if 'versionPattern' in item:
            match = re.fullmatch(item['versionPattern'], current)
            if not match or 'version' not in match.groupdict():
                raise Failure('Runtime version output did not match the declared versionPattern')
            current = match['version']
        if not current or '\n' in current or '\r' in current or version(current) is None:
            raise Failure('Runtime command did not return one valid version')

    kind = item.get('provider', 'github')
    tag = ''
    try:
        if kind == 'github':
            tag, latest = client.release(
                item.get('repo', ''),
                tags=item.get('tags', False),
                **({'tagPattern': item['tagPattern']} if 'tagPattern' in item else {}),
            )
        else:
            latest = client.registry_release(item)
    except UnreadableSource as error:
        return Result(provider, name, source, 'skipped', current, detail=str(error))

    if kind in ('npm', 'crates'):
        if 'reporter' not in item:
            raise Failure('Semantic version comparison requires a reporter')
        document = command_json([item['reporter'], 'compare', current, latest], '.', client.timeout)
        rows = list(validate_adapter(document, provider, source))
        if len(rows) != 1 or rows[0].current != current or rows[0].latest != latest:
            raise Failure('Invalid semantic version comparison report')
        rows[0].name = name
        rows[0].version_url = registry_url(kind, item.get('project'), latest)
        rows[0].current_url = registry_url(kind, item.get('project'), current)
        return rows[0]

    result = compare(provider, name, source, current, latest)
    if result.state == 'unknown':
        result.state = 'skipped'
    result.version_url = (
        github_url(item.get('repo', ''), 'tree' if item.get('tags') else 'releases/tag', tag)
        if kind == 'github'
        else registry_url(kind, item.get('project'), latest)
    )
    if kind in ('pypi', 'npm', 'crates'):
        result.current_url = registry_url(kind, item.get('project'), current)
    return result


def input_owners(lock: NixLock) -> dict[str, set[str]]:
    nodes, root = lock['nodes'], lock['root']
    owners: dict[str, set[str]] = {}

    def resolve(edge: object, seen: tuple[tuple[str, ...], ...] = ()) -> str:
        if isinstance(edge, str):
            if edge not in nodes:
                raise Failure('Nix lock graph references a missing node')
            return edge
        if not isinstance(edge, list) or not all(isinstance(x, str) for x in cast(list[object], edge)):
            raise Failure('Invalid Nix follows reference')
        edge = cast(list[str], edge)
        key = tuple(edge)
        if key in seen:
            raise Failure('Cyclic Nix follows reference')
        node = root
        for part in edge:
            node = resolve(nodes[node].get('inputs', {})[part], seen + (key,))
        return node

    def walk(node: str, path: list[str], ancestors: set[str]) -> None:
        if node in ancestors:
            return
        for name, edge in nodes[node].get('inputs', {}).items():
            target = resolve(edge)
            nested = path + [name]
            owners.setdefault(target, set()).add('/'.join(nested))
            walk(target, nested, ancestors | {node})

    walk(root, [], set())
    return owners


def nix_jobs(config: Mapping[str, str], root: Path, client: Client) -> Iterator[SourceJob]:
    lock_file = config.get('lockFile', 'flake.lock')
    _ = relative(root, lock_file)
    _ = relative(root, 'flake.nix')
    with snapshot(root) as work:
        metadata = command_json(
            [
                config['exe'],
                '--extra-experimental-features',
                'nix-command flakes',
                'flake',
                'metadata',
                '--json',
                '--no-update-lock-file',
                '--no-write-lock-file',
                '--reference-lock-file',
                str(work / lock_file),
                'path:' + str(work),
            ],
            work,
            client.timeout,
        )
    lock = cast(NixMetadata, metadata)['locks']
    owners = input_owners(lock)

    if not owners:
        yield (
            'inputs',
            lambda: Result('nix', 'inputs', 'flake.lock', 'up-to-date', detail='Lockfile declares no external inputs'),
        )

    for node_name, names in sorted(owners.items()):
        node = lock['nodes'][node_name]
        if 'locked' not in node:
            raise Failure('Nix input has no locked source')
        label = ', '.join(sorted(names))
        yield label, lambda node=node, label=label: nix_input(node, label, config, root, client)


def nix_input(node: NixNode, label: str, config: Mapping[str, str], root: Path, client: Client) -> Result:
    if 'locked' not in node:
        raise Failure('Nix input has no locked source')
    locked, original = node['locked'], node.get('original', {})
    current = locked.get('rev', locked.get('narHash', ''))
    source = config.get('lockFile', 'flake.lock') + ':' + label
    owners = tuple(sorted({p.split('/')[0] for p in label.split(', ')}))
    detail = 'Update the owning top-level input: ' + ', '.join(owners)

    if original.get('rev'):
        row = Result('nix', label, source, 'pinned', current, detail='Explicit revision pin. ' + detail)
        if locked.get('type') == 'github':
            row.version_url = github_url(f'{locked["owner"]}/{locked["repo"]}', 'commit', current)
            row.current_url = row.version_url
        row.detail_identifiers = owners
        return row

    kind, ref = locked['type'], original.get('ref', 'HEAD')
    if kind == 'github':
        repo = f'{locked["owner"]}/{locked["repo"]}'
        tag = ref

        if version(ref) is not None:
            try:
                tag, _ = client.release(repo, tags=True)
            except UnreadableSource as error:
                return Result('nix', label, source, 'skipped', current, detail=str(error))

        latest = client.commit(repo, tag)
        return Result(
            'nix',
            label,
            source,
            'up-to-date' if current == latest else 'outdated',
            current,
            latest=latest,
            detail=f'{repo}: {ref} -> {tag}. {detail}',
            version_url=github_url(
                repo, 'tree' if version(ref) is not None else 'commit', tag if version(ref) is not None else latest
            ),
            current_url=github_url(repo, 'commit', current),
            detail_identifiers=(repo, *owners),
        )

    if kind == 'git':
        url = original.get('url', locked.get('url'))
        if not isinstance(url, str) or url.startswith(('file:', '/')):
            return Result(
                'nix', label, source, 'unknown', current, detail='Local Git inputs require an explicit adapter'
            )

        patterns = ['HEAD'] if ref == 'HEAD' else [f'refs/heads/{ref}', f'refs/tags/{ref}', f'refs/tags/{ref}^{{}}']
        output, _ = run([config['git'], 'ls-remote', '--', url, *patterns], root, client.timeout)
        rows = dict(line.split('\t', 1)[::-1] for line in output.splitlines())
        matches = {rows[p] for p in patterns if p in rows}

        if f'refs/tags/{ref}^{{}}' in rows and (direct := rows.get(f'refs/tags/{ref}')):
            matches.discard(direct)

        if len(matches) != 1:
            raise Failure('Git ref is missing or ambiguous')

        latest = matches.pop()
        return Result(
            'nix',
            label,
            source,
            'up-to-date' if current == latest else 'outdated',
            current,
            latest=latest,
            detail=detail,
            detail_identifiers=owners,
        )

    return Result(
        'nix', label, source, 'unknown', current, detail=f'Unsupported/local lock source: {kind}; supply an adapter'
    )


def workflow_jobs(config: Mapping[str, str], root: Path, client: Client) -> Iterator[SourceJob]:
    directory = relative(root, config.get('path', '.github/workflows'), directory=True)
    paths = sorted(set(directory.glob('*.yml')) | set(directory.glob('*.yaml')))

    if not paths:
        raise Failure('No workflow files found in the enabled directory')

    refs: dict[str, set[str]] = {}
    for path in paths:
        workflow = cast(object, yaml.load(path.read_text(), Loader=yaml.BaseLoader))

        if not isinstance(workflow, dict) or not isinstance(cast(dict[str, object], workflow).get('jobs'), dict):
            raise Failure('Invalid workflow jobs mapping')

        for value in cast(dict[str, object], workflow['jobs']).values():
            if not isinstance(value, dict):
                raise Failure('Invalid workflow job')
            job = cast(WorkflowJob, cast(object, value))
            uses = ([job['uses']] if 'uses' in job else []) + [
                step['uses'] for step in job.get('steps', []) if 'uses' in step
            ]

            for value in uses:
                if not isinstance(value, str):
                    raise Failure('Invalid workflow uses reference')
                refs.setdefault(value, set()).add(str(path.relative_to(root)))

    for ref, owners in sorted(refs.items()):
        source = ', '.join(sorted(owners))
        yield ref, lambda ref=ref, source=source: action(ref, source, client)

    if not refs:
        yield (
            'workflows',
            lambda: Result(
                'githubActions',
                'workflows',
                str(directory.relative_to(root)),
                'up-to-date',
                detail='No external action references',
            ),
        )


def action(ref: str, source: str, client: Client) -> Result:
    if ref.startswith('./'):
        return Result(
            'githubActions', ref, source, 'skipped', detail='Local action/workflow; versioned with this repository'
        )

    if ref.startswith('docker://') or '${{' in ref:
        return Result(
            'githubActions', ref, source, 'unknown', detail='Container or dynamic reference requires an adapter'
        )

    match = re.fullmatch(r'([\w.-]+/[\w.-]+)(?:/[^@]+)?@([^\s]+)', ref)
    if not match:
        raise Failure('Unsupported action reference')

    repo, current = match.groups()
    try:
        tag, _ = client.release(repo)
    except UnreadableSource as error:
        return Result('githubActions', ref, source, 'skipped', current, detail=str(error))
    before, after = client.commit(repo, current), client.commit(repo, tag)
    return Result(
        'githubActions',
        ref,
        source,
        'up-to-date' if before == after else 'outdated',
        current,
        latest=tag,
        detail='Compared resolved commits; a newer release may require migration',
        version_url=github_url(repo, 'releases/tag', tag),
        current_url=github_url(repo, 'commit', before),
    )
