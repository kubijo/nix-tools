"""Record project-checker invocations."""

import json
import os
import sys
from pathlib import Path

with Path(os.environ['DEPTRY_RECORD']).open('a') as stream:
    json.dump({'cwd': str(Path.cwd()), 'args': sys.argv[1:], 'env': dict(os.environ)}, stream)
    _ = stream.write('\n')
