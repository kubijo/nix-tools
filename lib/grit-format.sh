#!/usr/bin/env bash
set -euo pipefail

if (($# < 2)); then
    echo "grit-format: expected a Grit executable, options, --, and files" >&2
    exit 2
fi

tool=$1
shift

tool_options=()
separator=false
while (($# > 0)); do
    if [[ $1 == -- ]]; then
        separator=true
        shift
        break
    fi
    tool_options+=("$1")
    shift
done

if [[ $separator == false ]]; then
    echo "grit-format: missing -- before file arguments" >&2
    exit 2
fi

if (($# == 0)); then
    exit 0
fi

state=$(mktemp -d "${TMPDIR:-/tmp}/grit-format.XXXXXX")
replacements=()
cleanup() {
    rm -rf -- "$state"
    for replacement in "${replacements[@]}"; do
        [[ -z $replacement ]] || rm -f -- "$replacement"
    done
}
trap cleanup EXIT

pattern_dir="$state/work/.grit/patterns"
export HOME="$state/home"
export XDG_CACHE_HOME="$state/cache"
export GRIT_CACHE_DIR="$state/grit-cache"
export GRIT_TELEMETRY_DISABLED=true
mkdir -p "$pattern_dir" "$HOME" "$XDG_CACHE_HOME" "$GRIT_CACHE_DIR"

sources=("$@")
staged=()
for index in "${!sources[@]}"; do
    source=${sources[$index]}
    if [[ ! -f $source ]]; then
        echo "grit-format: not a regular file: $source" >&2
        exit 1
    fi

    target="$pattern_dir/pattern_$index.md"
    {
        printf '# nix-tools formatter input\n\n```grit\n'
        while IFS= read -r line || [[ -n $line ]]; do
            printf '%s\n' "$line"
        done <"$source"
        printf '```\n'
    } >"$target"
    staged+=("$target")
done

if ! (
    cd "$state/work"
    "$tool" patterns test
) >"$state/preflight.out" 2>&1; then
    cat "$state/preflight.out" >&2
    exit 1
fi

set +e
(
    cd "$state/work"
    "$tool" "${tool_options[@]}"
) >"$state/format.out" 2>&1
format_status=$?
set -e

# Grit uses status 1 to report that --write made formatting changes.
if ((format_status > 1)); then
    cat "$state/format.out" >&2
    exit "$format_status"
fi

if ! (
    cd "$state/work"
    "$tool" patterns test
) >"$state/postflight.out" 2>&1; then
    cat "$state/format.out" "$state/postflight.out" >&2
    exit 1
fi

formatted_dir="$state/formatted"
mkdir -p "$formatted_dir"
for index in "${!sources[@]}"; do
    extracted="$formatted_dir/$index.grit"
    inside=false
    complete=false
    while IFS= read -r line || [[ -n $line ]]; do
        if [[ $inside == false && $line == '```grit' ]]; then
            inside=true
        elif [[ $inside == true && $line == '```' ]]; then
            complete=true
            break
        elif [[ $inside == true ]]; then
            printf '%s\n' "$line" >>"$extracted"
        fi
    done <"${staged[$index]}"

    if [[ $complete == false ]]; then
        echo "grit-format: formatter output lost its Grit fence" >&2
        exit 1
    fi
    staged[index]=$extracted
done

for index in "${!sources[@]}"; do
    source=${sources[$index]}
    if cmp --silent -- "${staged[$index]}" "$source"; then
        replacements[index]=""
        continue
    fi
    replacement=$(mktemp "$(dirname -- "$source")/.grit-format.XXXXXX")
    cp -- "${staged[$index]}" "$replacement"
    chmod --reference="$source" -- "$replacement"
    replacements+=("$replacement")
done

for index in "${!sources[@]}"; do
    replacement=${replacements[$index]}
    [[ -z $replacement ]] || mv -- "$replacement" "${sources[$index]}"
done
