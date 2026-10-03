"""Bounded release lookups; errors never include credential-bearing URLs."""

import functools
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from common import Failure, UnreadableSource
from releases import source


class GitHubAccessFailure(Failure):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward registry/API credentials to another origin or downgrade TLS.
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)

        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise Failure('Cross-origin release redirect rejected')

        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, timeout=45, github='https://api.github.com', nvchecker='nvchecker', preflight=False):
        self.timeout = timeout
        self.base = github.rstrip('/')
        self.nvchecker = nvchecker
        self.get = functools.lru_cache(maxsize=256)(self._get)
        self.preflight = preflight and self.base == 'https://api.github.com'
        self._github_lock = threading.Lock()
        self._github_checked = False
        self._github_failure = None

    @staticmethod
    def _http_failure(code, headers, github):
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

    def _check_github_quota(self):
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
                resources = status.get('resources') if isinstance(status, dict) else None
                core = resources.get('core') if isinstance(resources, dict) else None
                if isinstance(core, dict) and type(core.get('remaining')) is int and core['remaining'] == 0:
                    headers = {
                        'x-ratelimit-remaining': '0',
                        'x-ratelimit-reset': str(core.get('reset', '')),
                    }
                    self._github_failure = self._http_failure(403, headers, True)
                self._github_checked = True
            if self._github_failure:
                raise self._github_failure

    def _get(self, url, *, github=False):
        headers = {'User-Agent': 'nix-tools-outdated/1', 'Accept': 'application/json'}
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if github and token:
            headers['Authorization'] = f'Bearer {token}'
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.build_opener(SafeRedirect()).open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise self._http_failure(error.code, error.headers, github) from None
        except OSError, ValueError:
            raise Failure('Release lookup failed or returned invalid JSON') from None

    def api(self, endpoint):
        self._check_github_quota()
        return self.get(f'{self.base}/{endpoint}', github=True)

    def release(self, repo, **policy):
        if self.base != 'https://api.github.com':
            raise UnreadableSource('nvchecker release discovery does not support this custom GitHub API base')
        self._check_github_quota()
        return source(self.nvchecker, {'provider': 'github', 'repo': repo, **policy}, self.timeout)

    def registry_release(self, item):
        return source(self.nvchecker, item, self.timeout)[1]

    def commit(self, repo, ref):
        value = self.api(f'repos/{repo}/commits/{urllib.parse.quote(ref, safe="")}')['sha']
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40,64}', value):
            raise Failure('Invalid upstream commit identifier')
        return value
