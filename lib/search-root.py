"""Validate an optional discovery directory without suppressing filesystem errors."""

import os
import stat
import sys


def main():
    try:
        mode = os.stat(sys.argv[1]).st_mode
    except FileNotFoundError:
        return 3
    if not stat.S_ISDIR(mode):
        raise ValueError(f'not a discovery directory: {sys.argv[1]}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f'file discovery failed: {error}', file=sys.stderr)
        sys.exit(1)
