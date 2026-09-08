#!/usr/bin/env bash
set -euo pipefail

if (($# != 0)); then
    echo "$GRIT_RUNNER_NAME: runtime arguments are not supported; configure the profile instead" >&2
    exit 2
fi

state=$("$GRIT_MKTEMP" -d "${TMPDIR:-/tmp}/$GRIT_RUNNER_NAME.XXXXXX")
trap '"$GRIT_RM" -rf -- "$state"' EXIT

export HOME="$state/home"
export XDG_CACHE_HOME="$state/cache"
export GRIT_CACHE_DIR="$state/grit-cache"
"$GRIT_MKDIR" -p "$HOME" "$XDG_CACHE_HOME" "$GRIT_CACHE_DIR"

common_args=()
check_args=()
apply_args=()
mapfile -t common_args <"$GRIT_COMMON_ARGS_FILE"
mapfile -t check_args <"$GRIT_CHECK_ARGS_FILE"
mapfile -t apply_args <"$GRIT_APPLY_ARGS_FILE"

if [[ $GRIT_RUNNER_MODE == test ]]; then
    cd "$GRIT_PATTERN_ROOT"
    "$GRIT_EXE" "${common_args[@]}" patterns test
    exit 0
fi

root=$PWD
while [[ ! -e "$root/$GRIT_TREE_ROOT_FILE" ]]; do
    if [[ $root == / ]]; then
        echo "$GRIT_RUNNER_NAME: could not find project root marker '$GRIT_TREE_ROOT_FILE' above '$PWD'" >&2
        exit 2
    fi
    root=${root%/*}
    [[ -n $root ]] || root=/
done

paths=()
mapfile -t paths <"$GRIT_PATHS_FILE"

fd_args=(
    --unrestricted
    --type file
    --print0
    --absolute-path
    --exclude .git
    --ignore-file "$GRIT_EXCLUDES_FILE"
)

candidates="$state/candidates"
set +e
(
    cd "$root"
    "$GRIT_FD" "${fd_args[@]}" -- . "${paths[@]}"
) >"$candidates"
selector_status=$?
set -e

if ((selector_status != 0)); then
    # fd uses 1 both for ordinary errors and an absent search root. Reserve 1 for
    # Grit policy findings so the aggregate can classify selection failures.
    if ((selector_status == 1)); then
        exit 2
    fi
    exit "$selector_status"
fi

"$GRIT_SORT" --zero-terminated --unique --output="$candidates" "$candidates"

selected=()
mapfile -d '' -t selected <"$candidates"
if ((${#selected[@]} == 0)); then
    echo "$GRIT_RUNNER_NAME: no files matched the configured paths"
    exit 0
fi

cd "$GRIT_PATTERN_ROOT"
case $GRIT_RUNNER_MODE in
check)
    grit_args=("${common_args[@]}" check --no-cache --level info "${check_args[@]}")
    ;;
apply)
    grit_args=("${common_args[@]}" check --no-cache --level info --fix "${apply_args[@]}")
    ;;
*)
    echo "$GRIT_RUNNER_NAME: invalid runner mode '$GRIT_RUNNER_MODE'" >&2
    exit 2
    ;;
esac

"$GRIT_EXE" "${grit_args[@]}" -- "${selected[@]}"
