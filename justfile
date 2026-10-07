import 'just/common.just'

mod test 'tests/justfile'

[private]
default:
    @just --justfile {{ justfile() }} --list

# Format the tree in place.
format *args:
    {{ nix_dev }} repofmt {{ args }}

# Lint the tree. Quiet unless something fails.
lint *args:
    {{ nix_dev }} repochk {{ args }}

# Type-check Python helpers and tests.
[no-exit-message]
typecheck:
    @{{ nix_dev }} bash -o pipefail -c 'basedpyright --project "{{ flake_dir }}/pyproject.toml" --pythonpath "$UV_PYTHON" --outputjson | "$UV_PYTHON" "{{ flake_dir }}/lib/basedpyright_report.py" "{{ flake_dir }}"'

# Check Python lock consistency offline.
[no-exit-message]
lockcheck:
    @{{ nix_dev }} bash -c 'uv lock --project "{{ flake_dir }}" --quiet --check --offline --no-cache --python "$UV_PYTHON"'

# Audit this checkout with the same app consumers run; deliberately separate from cached checks.
outdated *args:
    nix run {{ flake_dir }}#outdated -- --root {{ flake_dir }} {{ args }}

# Run flake checks; pass -L for full build logs.
[no-exit-message]
check *args:
    @nix flake check {{ flake_dir }} --keep-going --quiet --option warn-dirty false {{ args }}

# `flake check` skips systems it cannot build, so only this reads every one of them.
outputs:
    @nix flake show {{ flake_dir }} --all-systems --quiet --option warn-dirty false

# Fail if a fixture is gitignored, since nix reads the git tree and would never see it.
tracked:
    #!/usr/bin/env bash
    set -euo pipefail
    ignored=$(git -C {{ flake_dir }} ls-files --others --ignored --exclude-standard -- tests)
    if [[ -n $ignored ]]; then
        printf 'gitignored files under tests/:\n%s\n' "$ignored" >&2
        exit 1
    fi

# Run local checks, then flake checks.
[no-exit-message]
validate:
    #!/usr/bin/env bash
    set -uo pipefail

    if [[ -z ${IN_NIX_SHELL:-} ]]; then
        exec nix develop '{{ flake_dir }}' --option warn-dirty false --command just --justfile '{{ justfile() }}' validate
    fi

    failed=0
    just tracked || failed=1
    just format --ci || failed=1
    just lint || failed=1
    just lockcheck || failed=1
    just typecheck || failed=1
    if ((failed)); then
        exit "$failed"
    fi

    just check || failed=1
    just outputs >/dev/null || failed=1
    exit $failed

# Bump the pinned tool set. Formatter output moves with it, so this is a release.
update:
    nix flake update --flake {{ flake_dir }}

# Enter the dev shell.
shell:
    nix develop {{ flake_dir }}

# Remove build products and tool scratch.
clean:
    rm -rf {{ flake_dir }}/.tmp {{ flake_dir }}/result {{ flake_dir }}/result-*

# Cut a release: bump LEVEL (major|minor|patch), date the changelog, commit and tag. Does not push.
release level:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ flake_dir }}

    case "{{ level }}" in
        major | minor | patch) ;;
        *)
            echo "level must be major, minor or patch" >&2
            exit 1
            ;;
    esac

    if [[ -n $(git status --porcelain) ]]; then
        echo "working tree is dirty" >&2
        exit 1
    fi

    # No tags yet means the first bump lands on 0.1.0 rather than on nothing.
    latest=$(git tag --list 'v*' --sort=-v:refname | head -1)
    IFS=. read -r major minor patch <<< "${latest#v}"
    major=${major:-0} minor=${minor:-0} patch=${patch:-0}

    case "{{ level }}" in
        major) major=$((major + 1)) minor=0 patch=0 ;;
        minor) minor=$((minor + 1)) patch=0 ;;
        patch) patch=$((patch + 1)) ;;
    esac
    version="$major.$minor.$patch"

    # The tag message is the release notes, so an empty Unreleased section is nothing to cut.
    notes=$(awk '/^## \[Unreleased\]/ { f = 1; next } /^## / { f = 0 } f' CHANGELOG.md)
    if [[ -z ${notes//[[:space:]]/} ]]; then
        echo "nothing recorded under '## [Unreleased]'" >&2
        exit 1
    fi

    just validate

    read -rp "tag v$version? [y/N] " reply
    [[ $reply == [yY]* ]] || exit 1

    sed -i "s/^## \[Unreleased\]$/## [Unreleased]\n\n## [$version] - $(date +%F)/" CHANGELOG.md
    git add CHANGELOG.md
    git commit -m "Release v$version"
    # Annotated, since `git push --follow-tags` skips lightweight tags without a word.
    git tag -a "v$version" -m "v$version" -m "$notes"
    echo "tagged v$version — push it with: git push --follow-tags"
