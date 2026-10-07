"""Redirect the real nvchecker plugins to a loopback server, without replacing parsers."""

import os
import sys
from urllib.parse import urlsplit

# Imports come from the pinned nvchecker runtime.
from nvchecker.__main__ import main  # pyright: ignore[reportMissingModuleSource]
from nvchecker.util import AsyncCache  # pyright: ignore[reportMissingModuleSource]
from nvchecker_source import npm  # pyright: ignore[reportMissingModuleSource]

endpoint = os.environ['OUTDATED_TEST_REGISTRY']
original = AsyncCache.get_json


async def local_json(self: AsyncCache, url: str, *, headers: dict[str, str] | None = None) -> object:
    parsed = urlsplit(url)
    return await original(self, endpoint + parsed.path, headers=headers or {})


AsyncCache.get_json = local_json
npm.NPM_URL = endpoint + '/%s'
sys.exit(main())
