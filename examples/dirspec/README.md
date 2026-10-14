# dirspec examples

Run from the repository root. Each scenario uses a fresh temporary copy of [tree](tree), checked against the same
[prescription](prescription.toml).

```sh
uv run python examples/dirspec/run.py
uv run python examples/dirspec/run.py passing
uv run python examples/dirspec/run.py permissions
uv run python examples/dirspec/run.py --help
```

Each example has a separated heading, a description, and its expected outcome before the checker output. The runner
prints the actual exit code below the summary. It exits `0` when every selected scenario produces its expected code,
including deliberate failures; an unexpected result exits `1`.

| Scenario       | Demonstrates                                                     | Checker exit |
| -------------- | ---------------------------------------------------------------- | ------------ |
| `passing`      | Required paths, metadata, a valid symlink, and an excluded cache | `0`          |
| `extras`       | Strict checking rejects an undeclared file                       | `1`          |
| `allow-extras` | `--no-strict` permits that file, marked `+`                      | `0`          |
| `permissions`  | Owner execute permission is required                             | `1`          |
| `missing`      | A required README is absent                                      | `1`          |
| `forbidden`    | `count = 0` forbids source maps                                  | `1`          |
| `counts`       | Three JSON files exceed `max = 2`                                | `1`          |
| `dangling`     | A link has the wrong target and cannot resolve                   | `1`          |
| `size`         | `min_size = 1` rejects an empty file                             | `1`          |

The passing example prints this tree; the temporary root path varies:

```text
✓ …/tree/
├── ✓ README.txt
├── ✓ bin/
│   └── ✓ app
├── ✓ current -> bin/app
└── ✓ share/
    └── ✓ config.json

PASS  6 paths, 8 rules, 0 failures, 0 undeclared
Checker exit: 0 (expected 0).
```

The `permissions` example marks `bin/app` and its parents with `✗`, followed by:

```text
Failures
  bin/app
    mode_required: expected bits 0o0100, got 0o0644
    rule: 'bin/*'

FAIL  6 paths, 8 rules, 1 failure, 0 undeclared
Checker exit: 1 (expected 1).
```

Check the sample directly with the installed tool:

```sh
dirspec examples/dirspec/tree --spec examples/dirspec/prescription.toml
```

Git preserves the sample executable bit and `current` symlink. The runner restores modes on its temporary copies,
including copies from the Nix store. Ownership fields remain commented out because account names and IDs vary between
hosts. See the [reference](../../docs/dirspec.md) for the full format.
