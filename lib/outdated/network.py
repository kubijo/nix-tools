"""Bounded release lookups; errors never include credential-bearing URLs."""

import functools
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from email.message import Message
from http.client import HTTPMessage, HTTPResponse
from typing import IO, Protocol, cast, override

from common import Failure, UnreadableSource
from releases import ReleaseSource, source


class GitHubAccessFailure(Failure):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> urllib.request.Request | None:
        # Never forward registry/API credentials to another origin or downgrade TLS.
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)

        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise Failure('Cross-origin release redirect rejected')

        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Get(Protocol):
    def __call__(self, url: str, *, github: bool = False) -> object: ...


class Client:
    def __init__(
        self,
        timeout: float = 45,
        github: str = 'https://api.github.com',
        nvchecker: str = 'nvchecker',
        preflight: bool = False,
    ) -> None:
        self.timeout: float = timeout
        self.base: str = github.rstrip('/')
        self.nvchecker: str = nvchecker
        self.get: Get = functools.lru_cache(maxsize=256)(self._get)
        self.preflight: bool = preflight and self.base == 'https://api.github.com'
        self._github_lock: threading.Lock = threading.Lock()
        self._github_checked: bool = False
        self._github_failure: Failure | None = None

    @staticmethod
    def _http_failure(code: int, headers: Mapping[str, str] | Message | None, github: bool) -> Failure:
        headers = headers or {}
        if github and code in (403, 429) and headers.get('x-ratelimit-remaining') == '0':
            reset = headers.get('x-ratelimit-reset', '')
            try:
                when = datetime.fromtimestamp(int(reset), UTC).strftime('%Y-%m-%d %H:%M UTC')
            except OverflowError, OSError, ValueError:
                when = 'the GitHub rate-limit reset'
            return GitHubAccessFailure(f'GitHub API quota exhausted until {when}; set GH_TOKEN or GITHUB_TOKEN')
        if github and code == 401:
            return GitHubAccessFailure('GitHub API authentication failed (HTTP 401); check GH_TOKEN or GITHUB_TOKEN')
        return Failure(f'Release lookup returned HTTP {code}')

    def _check_github_quota(self) -> None:
        if not self.preflight:
            return
        with self._github_lock:
            if not self._github_checked:
                try:
                    status = self._get(f'{self.base}/rate_limit', github=True)
                except GitHubAccessFailure as error:
                    self._github_failure = error
                    status = None
                except Failure:
                    status = None
                resources = cast(dict[str, object], status).get('resources') if isinstance(status, dict) else None
                core = cast(dict[str, object], resources).get('core') if isinstance(resources, dict) else None
                core = cast(dict[str, object], core) if isinstance(core, dict) else None
                if isinstance(core, dict) and type(core.get('remaining')) is int and core['remaining'] == 0:
                    headers = {
                        'x-ratelimit-remaining': '0',
                        'x-ratelimit-reset': str(core.get('reset', '')),
                    }
                    self._github_failure = self._http_failure(403, headers, True)
                self._github_checked = True
            if self._github_failure:
                raise self._github_failure

    def _get(self, url: str, *, github: bool = False) -> object:
        headers = {'User-Agent': 'nix-tools-outdated/1', 'Accept': 'application/json'}
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if github and token:
            headers['Authorization'] = f'Bearer {token}'
        request = urllib.request.Request(url, headers=headers)
        try:
            with cast(
                HTTPResponse, urllib.request.build_opener(SafeRedirect()).open(request, timeout=self.timeout)
            ) as response:
                return cast(object, json.load(response))
        except urllib.error.HTTPError as error:
            raise self._http_failure(error.code, error.headers, github) from None
        except OSError, ValueError:
            raise Failure('Release lookup failed or returned invalid JSON') from None

    def api(self, endpoint: str) -> object:
        self._check_github_quota()
        return self.get(f'{self.base}/{endpoint}', github=True)

    def release(self, repo: str, *, tags: bool = False, tagPattern: str | None = None) -> tuple[str, str]:
        if self.base != 'https://api.github.com':
            raise UnreadableSource('nvchecker release discovery does not support this custom GitHub API base')
        self._check_github_quota()
        policy: ReleaseSource = {'provider': 'github', 'repo': repo, 'tags': tags}
        if tagPattern is not None:
            policy['tagPattern'] = tagPattern
        return source(self.nvchecker, policy, self.timeout)

    def registry_release(self, item: ReleaseSource) -> str:
        return source(self.nvchecker, item, self.timeout)[1]

    def commit(self, repo: str, ref: str) -> str:
        document = self.api(f'repos/{repo}/commits/{urllib.parse.quote(ref, safe="")}')
        value = cast(dict[str, object], document)['sha']
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40,64}', value):
            raise Failure('Invalid upstream commit identifier')
        return value
