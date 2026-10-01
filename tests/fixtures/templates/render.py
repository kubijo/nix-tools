import configparser
import json
from pathlib import Path

import jinja2
import yaml

context = dict(enabled=True, name="Example", names=["one", "two"])
environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
rendered = {
    path.name: environment.from_string(path.read_text()).render(context)
    for path in Path(".").iterdir()
    if path.suffix in (".sls", ".j2", ".jinja")
}
assert yaml.safe_load(rendered["state.sls"])["hello"]["test.nop"] == [
    {"name": "Example"}
]
assert json.loads(rendered["data.j2"]) == {"names": ["one", "two"]}
unit = configparser.ConfigParser()
unit.read_string(rendered["service.jinja"])
assert unit["Service"]["ExecStart"] == "/bin/true"
