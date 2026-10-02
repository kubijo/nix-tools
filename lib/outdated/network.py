"""Bounded release lookups; errors never include credential-bearing URLs."""

import functools
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from common import Failure, UnreadableSource
from releases import source


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward registry/API credentials to another origin or downgrade TLS.
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)

        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise Failure('Cross-origin release redirect rejected')

        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, timeout=45, github='https://api.github.com', nvchecker='nvchecker'):
        self.timeout = timeout
        self.base = github.rstrip('/')
        self.nvchecker = nvchecker
        self.get = functools.lru_cache(maxsize=256)(self._get)

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
            raise Failure(f'Release lookup returned HTTP {error.code}') from None
        except OSError, ValueError:
            raise Failure('Release lookup failed or returned invalid JSON') from None

    def api(self, endpoint):
        return self.get(f'{self.base}/{endpoint}', github=True)

    def release(self, repo, **policy):
        if self.base != 'https://api.github.com':
            raise UnreadableSource('nvchecker release discovery does not support this custom GitHub API base')
        return source(self.nvchecker, {'provider': 'github', 'repo': repo, **policy}, self.timeout)

    def registry_release(self, item):
        return source(self.nvchecker, item, self.timeout)[1]

    def commit(self, repo, ref):
        value = self.api(f'repos/{repo}/commits/{urllib.parse.quote(ref, safe="")}')['sha']
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40,64}', value):
            raise Failure('Invalid upstream commit identifier')
        return value
