"""Record PHPStan arguments and the runtime visible to an executable override."""

import json
import os
import shutil
import sys
from pathlib import Path

with Path(os.environ['PHPSTAN_RECORD']).open('a') as stream:
    json.dump(
        {'cwd': str(Path.cwd()), 'args': sys.argv[1:], 'php': shutil.which('php'), 'env': dict(os.environ)}, stream
    )
    _ = stream.write('\n')
