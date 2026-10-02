"""Redirect the real nvchecker plugins to a loopback server, without replacing parsers."""

import os
import sys
from urllib.parse import urlsplit

from nvchecker.__main__ import main
from nvchecker.util import AsyncCache
from nvchecker_source import npm

endpoint = os.environ['OUTDATED_TEST_REGISTRY']
original = AsyncCache.get_json


async def local_json(self, url, *args, **kwargs):
    parsed = urlsplit(url)
    return await original(self, endpoint + parsed.path, *args, **kwargs)


AsyncCache.get_json = local_json
npm.NPM_URL = endpoint + '/%s'
sys.exit(main())
