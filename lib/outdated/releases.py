"""Consume nvchecker's documented JSON events; source parsing belongs to nvchecker."""

import json
import os
import pathlib
import re
import shlex
import tempfile
from collections.abc import Mapping
from typing import TypedDict, cast

from common import Failure, UnreadableSource, run, version


class ReleaseSource(TypedDict, total=False):
    provider: str
    repo: str
    url: str
    project: str
    tags: bool
    tagPattern: str


def lookup(exe: str, settings: Mapping[str, str | bool], timeout: float) -> str:
    # No oldver/newver: every invocation queries upstream and writes no result cache.
    with tempfile.TemporaryDirectory(prefix='nix-tools-release-') as temporary:
        config = pathlib.Path(temporary) / 'source.toml'
        config.touch(mode=0o600)
        _ = config.write_text(
            f'[__config__]\nhttp_timeout = {int(timeout)}\n[entry]\n'
            + ''.join(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in settings.items())
        )
        # nvchecker normally reports source failures through JSON while exiting 0.
        # We classify those below; any process-level failure is still fatal.
        output, _ = run([exe, '-c', str(config), '--logger=json'], temporary, timeout)
    try:
        values = [cast(object, json.loads(line)) for line in output.splitlines()]
    except ValueError:
        raise Failure('nvchecker returned invalid JSON') from None
    if not values or not all(isinstance(event, dict) for event in values):
        raise Failure('nvchecker returned an invalid report')
    events = cast(list[dict[str, object]], values)
    # An HTTP/authentication/command error can accompany a no-result event.
    # Never turn that error into a harmless skip or disclose raw native diagnostics.
    if any(event.get('level') == 'error' and event.get('event') != 'no-result' for event in events):
        raise Failure('nvchecker lookup failed; check source availability and runtime credentials')
    results = [event for event in events if event.get('event') in ('updated', 'up-to-date', 'no-result')]
    if len(results) != 1 or results[0].get('name') != 'entry':
        raise Failure('nvchecker returned an incomplete or ambiguous report')
    result = results[0]
    if result['event'] == 'no-result':
        raise UnreadableSource('nvchecker found no version matching the release policy')
    latest = result.get('version')
    if not isinstance(latest, str) or not latest.strip():
        raise Failure('nvchecker returned an invalid version field')
    return latest


def source(exe: str, item: ReleaseSource, timeout: float) -> tuple[str, str]:
    kind = item.get('provider', 'github')
    settings: dict[str, str | bool] = {}
    pattern = item.get('tagPattern')
    if kind == 'github':
        repo = item.get('repo', '')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise Failure('Expected an owner/repository GitHub identifier')
        settings = {'source': 'github', 'github': repo}
        if item.get('tags'):
            settings['use_max_tag'] = True
        elif pattern:
            settings['use_max_release'] = True
        else:
            settings['use_latest_release'] = True
        if token := os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'):
            settings['token'] = token
    elif kind == 'git':
        # nvchecker's git provider invokes a shell command. Quote the URL as one
        # argument and terminate options; never interpolate an unquoted source.
        if 'url' not in item:
            raise Failure('Git release source requires a URL')
        settings = {'source': 'git', 'git': '-- ' + shlex.quote(item['url'])}
    elif kind in ('npm', 'pypi', 'crates'):
        native = 'cratesio' if kind == 'crates' else kind
        if 'project' not in item:
            raise Failure('Registry release source requires a project')
        settings = {'source': native, native: item['project']}
    else:
        raise Failure('Unsupported release provider')

    if pattern:
        if kind not in ('github', 'git') or 'version' not in re.compile(pattern).groupindex:
            raise Failure('tagPattern requires a Git source and a named version group')
        settings['include_regex'] = pattern
    elif kind == 'git' or item.get('tags'):
        # A uniform stable dotted-version policy. Namespaced/unreadable tags are
        # skipped; package-specific extraction rules do not belong in the catalogue.
        settings['include_regex'] = r'v?\d+(?:\.\d+)*'

    raw = lookup(exe, settings, timeout)
    if pattern:
        match = re.fullmatch(pattern, raw)
        number = match['version'] if match else ''
    else:
        number = raw
    parsed = version(number)
    if parsed is None or parsed.is_prerelease or parsed.is_devrelease:
        raise UnreadableSource('Upstream did not provide a readable stable version; declare an explicit release policy')
    return raw, number
