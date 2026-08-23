#!/usr/bin/env bash
set -euo pipefail
target=${1:-.}
if [ -d "$target" ]
then
      printf 'dir %s\n' "$target"
  else
        printf 'not a dir\n'
fi
