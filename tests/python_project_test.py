"""The Nix environments must match the Python project and its lockfile."""

import importlib.metadata
import json
import pathlib
import subprocess
import sys
import tomllib

lock = tomllib.loads(pathlib.Path(sys.argv[1]).read_text())
assert sys.version_info[:2] == (3, 14)
for package in lock['package']:
    if 'registry' in package['source']:
        assert importlib.metadata.version(package['name']) == package['version'], package['name']

result = subprocess.run(
    [
        sys.argv[2],
        '-c',
        """
import importlib.util, json, sys
import packaging, yaml, tyro
assert sys.version_info[:2] == (3, 14)
print(json.dumps({name: importlib.util.find_spec(name) is not None for name in ('coverage', 'jinja2', 'editorconfig', 'ty')}))
""",
    ],
    check=True,
    capture_output=True,
    text=True,
)
assert not any(json.loads(result.stdout).values()), 'Development dependencies leaked into the runtime'
