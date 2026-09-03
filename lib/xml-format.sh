#!/usr/bin/env bash
set -euo pipefail

if (($# == 0)); then
    echo "xml-format: missing xmllint executable" >&2
    exit 2
fi

tool=$1
shift
args=()
delimiter=false
while (($# > 0)); do
    if [[ $1 == -- ]]; then
        delimiter=true
        shift
        break
    fi
    args+=("$1")
    shift
done

if [[ $delimiter == false ]]; then
    echo "xml-format: missing argument delimiter" >&2
    exit 2
fi

for src in "$@"; do
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT

    # Prefix root-level dash names because xmllint has no end-of-options marker.
    input=$src
    if [[ $input != /* && $input != ./* && $input != ../* ]]; then
        input=./$input
    fi

    if XMLLINT_INDENT='  ' "$tool" "${args[@]}" "$input" >"$tmp"; then
        if [[ ! -s $tmp ]]; then
            echo "xml-format: xmllint produced empty output for $src" >&2
            exit 1
        fi
    else
        status=$?
        exit "$status"
    fi

    # Write only a successful diff, retaining the source inode and its permissions.
    if ! cmp -s "$tmp" "$src"; then
        cat "$tmp" >"$src"
    fi
    rm -f "$tmp"
    trap - EXIT
done
