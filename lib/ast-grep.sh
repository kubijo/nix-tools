#!/usr/bin/env bash
set -euo pipefail

if (($# != 0)); then
    echo "$AST_GREP_RUNNER_NAME: runtime arguments are not supported; configure the profile instead" >&2
    exit 2
fi

root=$PWD
while [[ ! -e "$root/$AST_GREP_TREE_ROOT_FILE" ]]; do
    if [[ $root == / ]]; then
        echo "$AST_GREP_RUNNER_NAME: could not find project root marker '$AST_GREP_TREE_ROOT_FILE' above '$PWD'" >&2
        exit 1
    fi
    root=${root%/*}
    [[ -n $root ]] || root=/
done
cd "$root"

paths=()
excludes=()
mapfile -d '' -t paths < <("$AST_GREP_JQ" -j '.[] | ., "\u0000"' <<<"$AST_GREP_PATHS_JSON")
mapfile -d '' -t excludes < <("$AST_GREP_JQ" -j '.[] | ., "\u0000"' <<<"$AST_GREP_EXCLUDES_JSON")

argv=(
    scan
    --config "$root/$AST_GREP_CONFIG_FILE"
    --no-ignore=exclude
    --no-ignore=global
    --no-ignore=parent
)
for exclusion in "${excludes[@]}"; do
    argv+=(--globs "!$exclusion")
done

case $AST_GREP_MODE in
check)
    argv+=(--error)
    ;;
apply)
    argv+=(--update-all)
    ;;
*)
    echo "$AST_GREP_RUNNER_NAME: invalid runner mode '$AST_GREP_MODE'" >&2
    exit 2
    ;;
esac

exec "$AST_GREP_EXE" "${argv[@]}" -- "${paths[@]}"
