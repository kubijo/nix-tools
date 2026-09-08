#!/usr/bin/env bash
set -euo pipefail

if (($# != 0)); then
    echo "grit-check: runtime arguments are not supported; configure the profiles instead" >&2
    exit 2
fi

state=$("$GRIT_MKTEMP" -d "${TMPDIR:-/tmp}/grit-check.XXXXXX")
trap '"$GRIT_RM" -rf -- "$state"' EXIT

tests_run=0
scans_run=0
policy_failures=0
test_failures=0
runner_failures=0
color=false
terminal=false

if [[ -v NO_COLOR ]]; then
    unset CLICOLOR_FORCE
elif [[ -t 1 ]]; then
    export CLICOLOR_FORCE=1
    color=true
    terminal=true
elif [[ ${CLICOLOR_FORCE:-0} != 0 ]]; then
    color=true
fi

print_status() {
    local profile=$1 operation=$2 status=$3
    if [[ $color == true && $status == passed ]]; then
        printf '\033[2m[grit:%s:%s]\033[0m \033[32m%s\033[0m\n' \
            "$profile" "$operation" "$status"
    elif [[ $color == true ]]; then
        printf '\033[2m[grit:%s:%s]\033[0m %s\n' "$profile" "$operation" "$status"
    else
        printf '[grit:%s:%s] %s\n' "$profile" "$operation" "$status"
    fi
}

emit_output() {
    local profile=$1 operation=$2 output=$3 prefix
    if [[ $color == true ]]; then
        prefix=$'\033[2m'"[grit:$profile:$operation]"$'\033[0m '
    else
        prefix="[grit:$profile:$operation] "
    fi
    "$GRIT_SED" -u -e '/^$/d' -e "s/^/$prefix/" "$output"
}

show_running() {
    if [[ $terminal == true ]]; then
        printf '\033[2m[grit:%s:%s] running\033[0m\r' "$1" "$2"
    fi
}

clear_running() {
    if [[ $terminal == true ]]; then
        printf '\r\033[2K'
    fi
}

while IFS=$'\t' read -r profile operation program; do
    case $operation in
    test)
        tests_run=$((tests_run + 1))
        ;;
    check)
        scans_run=$((scans_run + 1))
        ;;
    *)
        print_status "$profile" "$operation" "invalid aggregate operation" >&2
        runner_failures=$((runner_failures + 1))
        continue
        ;;
    esac

    output="$state/$profile-$operation.out"
    show_running "$profile" "$operation"
    set +e
    "$program" >"$output" 2>&1
    status=$?
    set -e
    clear_running

    if ((status == 0)); then
        print_status "$profile" "$operation" passed
        continue
    fi

    printf '\n'
    set +e
    emit_output "$profile" "$operation" "$output"
    prefix_status=$?
    set -e

    if ((prefix_status != 0)); then
        runner_failures=$((runner_failures + 1))
        print_status "$profile" "$operation" "output prefixer failed with status $prefix_status"
    elif ((status == 1)) && [[ $operation == check ]]; then
        policy_failures=$((policy_failures + 1))
        print_status "$profile" "$operation" "policy violations found"
    elif ((status == 1)); then
        test_failures=$((test_failures + 1))
        print_status "$profile" "$operation" "pattern tests failed"
    else
        runner_failures=$((runner_failures + 1))
        print_status "$profile" "$operation" "runner failed with status $status"
    fi
done <"$GRIT_MANIFEST"

if ((policy_failures == 0 && test_failures == 0 && runner_failures == 0)); then
    printf 'grit-check: ok (profile tests: %d; gated scans: %d)\n' "$tests_run" "$scans_run"
    exit 0
fi

printf '\n'
if [[ $color == true ]]; then
    printf '\033[2mgrit-check: failed (policy failures: %d; pattern-test failures: %d; runner errors: %d)\033[0m\n' \
        "$policy_failures" "$test_failures" "$runner_failures"
else
    printf 'grit-check: failed (policy failures: %d; pattern-test failures: %d; runner errors: %d)\n' \
        "$policy_failures" "$test_failures" "$runner_failures"
fi

if ((runner_failures > 0)); then
    exit 2
fi
exit 1
