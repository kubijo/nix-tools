"""The Nix environments must match the Python project and its lockfile."""

import importlib.metadata
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import TypedDict, cast


class Package(TypedDict):
    name: str
    version: str
    source: dict[str, str]


class Lock(TypedDict):
    package: list[Package]


lock = cast(Lock, cast(object, tomllib.loads(pathlib.Path(sys.argv[1]).read_text())))
assert sys.version_info[:2] == (3, 14)
for package in lock['package']:
    if 'registry' in package['source']:
        assert importlib.metadata.version(package['name']) == package['version'], package['name']

result = subprocess.run(
    [
        sys.argv[2],
        '-c',
        """
import importlib.metadata, importlib.util, json, sys
import packaging, yaml, tyro
assert sys.version_info[:2] == (3, 14)
assert 'types-pyyaml' not in {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()}
print(json.dumps({name: importlib.util.find_spec(name) is not None for name in ('coverage', 'jinja2', 'editorconfig')}))
""",
    ],
    check=True,
    capture_output=True,
    text=True,
)
assert not any(cast(dict[str, bool], json.loads(result.stdout)).values()), (
    'Development dependencies leaked into the runtime'
)
