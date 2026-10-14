# dirspec

`dirspec` checks directory trees and file metadata against TOML prescriptions, then prints an annotated tree with
expected/actual diagnostics.

```sh
nix run github:kubijo/nix-tools#dirspec -- ./build --spec ./dirspec.toml
```

`TARGET` is required. The prescription filename is relative to the working directory; patterns are relative to `TARGET`.
A root symlink resolves to its directory; links within the tree are never traversed. Directory traversal uses no-follow
descriptors; a directory identity change aborts the check with an I/O error.

Try the [runnable examples](../examples/dirspec/README.md) for passing checks and deliberate failures:

```sh
uv run python examples/dirspec/run.py
```

## Prescription

```toml
version = 1
strict = false
exclude = [".git"]

[paths]
"README.md" = { type = "file" }
"dist/app.*.js" = { type = "file", count = 1 }
"dist/**/*.map" = { count = 0 }
"logs/*.log" = { type = "file", min = 0 }

[paths."bin/*"]
type = "file"
mode_required = 0o100
mode_forbidden = 0o022
min_size = 1
```

Require `version = 1` and a nonempty `paths` table. Unknown fields, invalid types, negative counts/sizes, and
contradictory bounds or permissions are errors.

Rules may use inline or expanded tables. All matching rules and their attributes apply, regardless of order.

### Patterns and counts

Patterns support `*`, `?`, `[ab]`, recursive `**`, and brace alternatives such as `*.{js,css}`. Matching is case
sensitive and includes hidden entries. `*` stays within one segment; `**/` spans zero or more directory levels.

Absolute patterns and `..` components are invalid. Leading `./` and trailing slashes are removed; require directories
with `type = "directory"`. Only an explicit `"."` rule matches the root.

Each rule requires at least one match by default. Counts include wrong-type matches.

| Field   | Behavior                                                    |
| ------- | ----------------------------------------------------------- |
| `count` | Exact number of matches; `0` forbids matching paths         |
| `min`   | Minimum matches; defaults to `1`; `0` makes a rule optional |
| `max`   | Maximum matches; defaults to no limit                       |

Use `count` or `min`/`max`, never both. Optional rules check present matches; `{ min = 0, max = 3 }` allows up to three.

Braces are alternatives: `i18n/{en,cs}.js` needs either language. Require both with separate rules.

### Metadata assertions

Only specified attributes are checked.

| Field                  | Accepted values and behavior                                         |
| ---------------------- | -------------------------------------------------------------------- |
| `type`                 | `any` (default), `file`, `directory`, or `symlink`                   |
| `mode`                 | Exact permission bits; use TOML octal, e.g. `0o755`                  |
| `mode_required`        | Every specified bit must be set; e.g. `0o100` requires owner execute |
| `mode_forbidden`       | No specified bit may be set; e.g. `0o022` forbids group/world write  |
| `owner`, `group`       | Account name or nonnegative numeric UID/GID                          |
| `size`                 | Exact regular-file size in bytes                                     |
| `min_size`, `max_size` | Inclusive regular-file size bounds; default lower bound is `0`       |
| `link_target`          | Exact stored symlink target text, without normalization              |
| `link_exists`          | Whether a symlink resolves to an existing target                     |

Modes accept `0o0000`–`0o7777`, including special bits. Exact and bit assertions must agree; symlink mode checks fail.
Use `size` or size bounds, never both. Size checks require regular files; `any` also accepts special file types.

Account names use the host database; unknown names are errors. Numeric IDs need no account. Symlink ownership checks use
the link itself.

Link assertions require symlinks. Dangling links pass unless `link_exists = true`; loops count as nonexistent targets.
Resolution may leave the tree, without traversing contents. I/O errors fail the invocation.

### Undeclared entries and exclusions

Undeclared entries pass by default and fail with `strict = true`. CLI `--strict` and `--no-strict` override the
prescription.

Parents of matched paths need no separate rules. Other empty directories need rules in strict mode; a directory rule
covers only that directory. Failed assertions do not make an entry undeclared.

`exclude` patterns remove entries from checks and output; matching directories prune their subtrees. Excluded paths
cannot satisfy rules, and `"."` cannot be excluded. Nothing is excluded implicitly, including `.git` or the
prescription. Exclude `"cache"` to prune its contents as well; `"cache/**"` alone can leave the directory undeclared.

## Output and integration

`✓` marks declared paths and structural parents, `+` permitted extras, and `✗` failures and their ancestors. Tree guides
and successful paths are muted; failed paths stand out in bold. Diagnostics follow the tree, grouped by path and rule,
with a separate pass/fail summary. Color is limited to status markers. Output is plain for pipes, CI, and agents;
`NO_COLOR` disables color and `FORCE_COLOR` overrides it.

When tree guides would consume the output width, entries appear as a list of full relative paths instead. Every entry
remains visible, including in redirected output.

Exit codes: `0` success, `1` assertion failures, `2` configuration/I/O errors.

Available on every supported system as `packages.<system>.dirspec`, `apps.<system>.dirspec`, and
`(nix-tools.lib.packagesFor pkgs).dirspec`. This repository's dev shell includes it.

Run it after a build or through `validate.steps`:

```nix
let
  dirspec = (nix-tools.lib.packagesFor pkgs).dirspec;
in
{
  validate.steps = [
    {
      name = "Build tree";
      run = "${pkgs.lib.getExe dirspec} build --spec dirspec.toml";
    }
  ];
}
```

It runs separately from `repochk` and hermetic lint checks. See the
[example prescription](../examples/dirspec/prescription.toml).
