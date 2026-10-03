# Outdated reports

`configure { outdated = true; ... }` adds `apps.outdated` and `packages.repo-outdated`. Nix inputs are enabled by
default; other inventories need explicit opt-in:

```nix
outdated = {
  uv = true;
  githubActions = true;
  cargo.root = "service";
  yarn.root = "frontend";
};
```

```sh
nix run .#outdated
nix run .#outdated -- --json
nix run .#outdated -- --root /path/to/project --no-color
```

The command finds `treeRootFile` upward. It runs online, outside cached `checks` and `validate`.

## Results and failure behavior

| Exit | Meaning                                                                                |
| ---- | -------------------------------------------------------------------------------------- |
| 0    | All requested inventories resolved; no outdated findings                               |
| 1    | Outdated findings, without incomplete lookups                                          |
| 2    | Error, unknown, blocked, missing input or empty report; takes precedence over findings |

`--json` emits `schemaVersion = 1`, `state`, `counts`, and rows with `provider`, `name`, `source`, `state`, `current`,
`compatible`, `latest`, `detail`. States: `up-to-date`, `outdated`, `ahead`, `pinned`, `skipped`, `unknown`, `blocked`,
`error`. Unknown sources never count as current. Skips and pins stay visible without failing the report. `latest` means
upstream availability; `compatible` is a candidate, not a tested upgrade.

TTY output has compact bordered tables, live progress, and known public release/ref links. Pipes, CI, and coding agents
get terse plain text. Agent detection checks nonempty `CLAUDECODE`, `CURSOR_AGENT`, `GEMINI_CLI`, `CODEX_THREAD_ID`,
`OPENCODE`, `IN_CLANKER`, or `in-clanker`. JSON retains full values. `NO_COLOR` or `--no-color` disables ANSI and live
progress; `FORCE_COLOR=1` overrides color and plain-output heuristics, while `FORCE_COLOR=0` disables color. Live
progress still requires a human TTY. Terse output puts the verdict last. Results keep configured order even when jobs
finish out of order.

## Configuration

Options: the providers below, `releases`, `adapters`, `skips`, `timeout` (45 seconds), `concurrency` (6, maximum 32),
and `githubApi` (default `https://api.github.com`; custom bases support commit lookups only). An attrset enables its
entry unless `enable = false`. Providers accept repository-relative `root` (default `.`); missing enabled inputs fail.
Native providers accept `package` or `exe`, which must satisfy the bundled client's CLI/JSON contract. Keep credentials
in the runtime environment or native client configuration.

| Provider        | Default | Source and limit                                                    |
| --------------- | ------- | ------------------------------------------------------------------- |
| `nix`           | On      | `flake.lock`; local sources need adapters.                          |
| `cargo`         | Off     | `Cargo.toml`/`Cargo.lock`; Git sources need policy.                 |
| `uv`            | Off     | `pyproject.toml`/`uv.lock`; Git/URL/path sources need policy.       |
| `npm`           | Off     | `package-lock.json`; no compatibility claim.                        |
| `pnpm`          | Off     | `pnpm-lock.yaml`; `wanted` is the compatible candidate.             |
| `yarn`          | Off     | Modern `yarn.lock`; Classic rejected, unsupported locators visible. |
| `composer`      | Off     | `composer.lock`; scripts/plugins disabled, platform policy honored. |
| `githubActions` | Off     | Workflow refs; local skipped, dynamic refs unknown.                 |

Nix reads the runtime lock, including uncommitted changes; `--override-input` does not rewrite it. UV omits
`latest_version` for both current packages and failed lookups, so those rows stay `unknown` (exit 2).

Native reports use disposable copies, omitting `.git`, installed environments, build outputs and caches. External or
cyclic source symlinks fail. Use a self-contained workspace root. Source and lock files remain unchanged; native
metadata caches may change.

GitHub uses runtime `GH_TOKEN` or `GITHUB_TOKEN`; exhausted API quota reports its reset time. Raw failed-command stderr
is withheld.

## Release entries

A release name replaces matching inline tool metadata.

```nix
outdated.releases = {
  application = {
    package = myApplication; # or version = "1.2.3";
    repo = "owner/application";
  };
  "lint.custom" = {
    version = "2.0.0";
    repo = "owner/custom-checker";
    tags = true;
    tagPattern = "release-(?P<version>[0-9]+[.][0-9]+[.][0-9]+)";
  };
  extraPythonTool = {
    provider = "pypi";
    project = "extra-tool";
    version = "1.0";
  };
  externalGitTool = {
    provider = "git";
    url = "https://example.org/tool.git";
    version = "1.0";
  };
  debputy = {
    package = (nix-tools.lib.packagesFor pkgs).debputy;
    provider = "git";
    url = "https://salsa.debian.org/debian/debputy.git";
    tagPattern = ''(?:archive/)?debian/(?P<version>[0-9]+(?:\.[0-9]+)+)'';
  };
};
```

Pass an overridden Debian package explicitly in the debputy release entry.

GitHub uses releases by default; `tags = true` selects tags. Nix version refs use tags. `tagPattern` needs a named
`version` group. Unreadable versions become `skipped`; lookup failures remain errors. See
[nvchecker's selection rules](https://nvchecker.readthedocs.io/en/latest/usage.html).

Release entries for `pypi`, `npm`, and `crates` use public registries, independently of project `.npmrc` or UV indexes.
Native dependency providers still honor those settings.

`versionCommand` replaces `version`. Its output must be one version or match an entire `versionPattern` with named
`version` group. It runs in a disposable copy, with disconnected stdin and the configured timeout.

```nix
lint.extraCheckers.css = {
  command = myCssCheck;
  includes = [ "*.css" ];
  outdated = { package = pkgs.biome; provider = "npm"; project = "@biomejs/biome"; };
};
lint.extraProjectCheckers.templates = {
  command = myTemplateCheck;
  outdated.skip = "Versioned with this repository";
};
```

Inline metadata uses release-entry fields; no source is inferred. It does not affect the tool or formatter cache.
Releases, adapters, and skips need distinct names. For a standalone static skip:
`outdated.skips."lint.local" = "Versioned with this repository";`.

## Command adapters

Keep domain policy in the consumer:

```nix
outdated.adapters.application = {
  package = myOutdatedAdapter; # or exe = lib.getExe myOutdatedAdapter;
  args = [ "--policy" "application" ];
  runtimeInputs = [ pkgs.git ];
  root = ".";
  timeout = 60;
};
```

Adapters replace matching inline entries. An unrelated release entry does not clear an unknown native-source row. The
runner calls `exe args... --root <disposable-copy>` there, with stdin disconnected. Adapters exit **0** on valid
reports, even when outdated, and emit one JSON document:

```json
{
  "schemaVersion": 1,
  "results": [
    {
      "name": "application",
      "state": "outdated",
      "current": "1.0",
      "latest": "2.0",
      "detail": "Stable channel"
    }
  ]
}
```

Rows allow string `name`, `state`, `current`, `compatible`, `latest`, `detail`; `name` and `state` are required. Unknown
fields or states, empty/malformed output, timeout, and nonzero exit fail.

Test the working checkout without changing the consumer pin:

```sh
nix run --override-input nix-tools path:/path/to/nix-tools --no-write-lock-file .#outdated -- --json
```

Inspect exit 2; compare source coverage with the prior report and run consumer checks.
