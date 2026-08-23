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

# Every check, naming each failure rather than stopping at the first.
check *args:
    nix flake check {{ flake_dir }} -L --keep-going --quiet {{ args }}

# `flake check` skips systems it cannot build, so only this reads every one of them.
outputs:
    nix flake show {{ flake_dir }} --all-systems --quiet

# Fail if a fixture is gitignored, since nix reads the git tree and would never see it.
tracked:
    #!/usr/bin/env bash
    set -euo pipefail
    ignored=$(git -C {{ flake_dir }} ls-files --others --ignored --exclude-standard -- tests)
    if [[ -n $ignored ]]; then
        printf 'gitignored files under tests/:\n%s\n' "$ignored" >&2
        exit 1
    fi

# Everything CI gates on, in one shell so every failure is reported.
validate:
    #!/usr/bin/env bash
    failed=0
    just tracked || failed=1
    just format --ci || failed=1
    just lint || failed=1
    just check || failed=1
    # The tree is for reading by hand; here only the verdict matters, and that is on stderr.
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
