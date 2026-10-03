# Outdated reports

`configure { outdated = true; ... }` adds `apps.outdated` and `repo-outdated` to `project.packages`. It enables Nix
input reporting. Other file-backed inventories are explicit opt-ins:

```nix
outdated = {
  enable = true;
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

The command discovers `treeRootFile` upward from the current directory. Every lookup runs when invoked. It is
intentionally absent from `checks` and default `validate`: an online verdict must not become a cached Nix build result.
Tool binaries remain ordinary cached derivations.

## Results and failure behavior

| Exit | Meaning                                                                                |
| ---- | -------------------------------------------------------------------------------------- |
| 0    | All requested inventories resolved; no outdated findings                               |
| 1    | Outdated findings, without incomplete lookups                                          |
| 2    | Error, unknown, blocked, missing input or empty report; takes precedence over findings |

JSON has `schemaVersion = 1`, aggregate `state`, state `counts` and `results`. Each result contains `provider`, `name`,
`source`, `state`, `current`, `compatible`, `latest` and `detail`. Missing version information is an empty string.
States are `up-to-date`, `outdated`, `ahead`, `pinned`, `skipped`, `unknown`, `blocked` and `error`. Pins and local
workspace packages remain visible; unsupported sources are `unknown`, never silently current. Unknown rows appear gray
in a terminal; JSON and `--no-color` remain plain. Results are ordered independently of request completion order. A
provider failure preserves completed results from other providers.

`latest` describes upstream availability according to the provider's policy. Only providers that establish a
requirement-compatible candidate populate `compatible`; even that is not a tested upgrade of your application.

## Configuration

Top-level fields: `enable` (default `false` when omitted from `configure`), the providers below, `releases`, `adapters`,
`skips`, `timeout` (45 seconds per request/command), `concurrency` (6 jobs, maximum 32), and `githubApi`
(`https://api.github.com`, overridable for commit lookups; release discovery on custom API bases is skipped).
Boolean/attrset toggles work like language tools; an attrset enables its entry unless `enable = false`.

Every provider accepts a repository-relative `root` (default `.`). Missing enabled inputs fail. Native package-manager
providers also accept `package` or `exe`; the executable must implement the pinned client's JSON/CLI contract. For npm,
it must be the npm JavaScript entrypoint (or a symlink to it), with its bundled libraries available. Disabled providers
do not force their packages. Credentials belong in runtime environment or native tool configuration, not Nix options or
store files.

| Provider        | Default | Inventory and policy                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| --------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `nix`           | On      | `flake.nix` and `flake.lock`, validated by Nix metadata without lock updates; `lockFile` overrides the filename. Traverses follows and transitive inputs, identifies the owning top-level input. GitHub version refs track stable tags, including repositories without GitHub Releases; branches/default refs track commit movement. Explicit revision pins are reported as pins. Git refs use `git ls-remote`; local/path and unsupported lock sources need an adapter. |
| `cargo`         | Off     | `Cargo.toml` and `Cargo.lock`; native Cargo metadata and `cargo-outdated` workspace resolution, separate compatible/latest columns. Git sources and dependency-graph changes require an explicit release policy. Uses the bundled Cargo/Rust toolchain.                                                                                                                                                                                                                  |
| `uv`            | Off     | `pyproject.toml` and `uv.lock`; universal locked inventory, all groups, declared registries. No environment created. Git/URL/path sources require a release policy. Requires an available Python interpreter satisfying UV's discovery requirements; managed-Python downloads are disabled.                                                                                                                                                                              |
| `npm`           | Off     | `package.json` and `package-lock.json`, read by npm's Arborist. npm configuration, registry access and SemVer use its bundled libraries. Includes nested/workspace entries and verifies locked tarball identity. Aliases use their real package names. No install required; no compatibility claim.                                                                                                                                                                      |
| `pnpm`          | Off     | `package.json` and `pnpm-lock.yaml`; native lock inventory and manifest reports, then an outdated query per declared dependency, preserving aliases and distinct workspace versions. `wanted` becomes `compatible`. Transitive dependencies remain owned by their parents.                                                                                                                                                                                               |
| `yarn`          | Off     | `package.json` and modern `yarn.lock` (Yarn 2+). A temporary Yarn plugin uses Yarn's project, descriptor, registry and SemVer APIs, respecting scoped registry/auth settings. Classic is rejected. Workspace entries are skipped; unsupported locator protocols are unknown. Range compatibility is not a complete peer/platform resolution. No installation or lock migration required.                                                                                 |
| `composer`      | Off     | `composer.json` and `composer.lock`, independently of `vendor`. Native complete locked report (`outdated --locked --all`) with scripts and plugins disabled. Latest respects Composer's platform policy. Development versions need an explicit release policy.                                                                                                                                                                                                           |
| `githubActions` | Off     | `path = ".github/workflows"`. Action tags and SHA pins are compared by resolved commit against the latest stable release. Handles action subpaths and reusable workflows; local actions are skipped, Docker/dynamic refs unknown. Runner images stay consumer policy.                                                                                                                                                                                                    |

Nix reporting reads the runtime lockfile, including uncommitted changes. It does not pretend that build-time
`--override-input` flags changed that file. The Nix section describes `flake.lock`.

Package-manager lockfiles are interpreted by their own tools/libraries. The Python adapters consume machine-readable
reports; they do not decode lockfiles. npm/Yarn version ordering uses upstream SemVer, Composer supplies its own
verdict, and Cargo supplies its own candidate selection. Unsupported pnpm specifier protocols remain unknown.

The pinned UV's tree report omits `latest_version` both for current packages and for some failed registry requests.
Those rows remain visible as `unknown` with exit status 2, including authenticated current packages. Absence is never
interpreted as current, and failed authentication cannot be mistaken for success.

Native project reports run against disposable copies. Copies omit `.git`, `.venv`, `node_modules`, `target`, `.tmp`,
`__pycache__` and Nix `result` links, and reject external/cyclic symlinks in remaining source. Select a self-contained
workspace root containing its members and local dependencies. The Python-environment provider performs read-only
inspection of the original interpreter. Project sources, staging, manifests, locks and environments are not updated.
Native metadata caches may change. UV uses temporary uncached lookups, npm requests fresh metadata, and other native
clients refresh through their reporting commands; no aggregate result is cached.

GitHub requests use `GH_TOKEN` or `GITHUB_TOKEN` at runtime. Release selection and HTTP handling belong to nvchecker;
commit lookups use the GitHub API directly. HTTP errors and rate limits remain errors. Reports strip terminal escapes
and redact credentials; raw failed-command stderr is withheld. Set native registry credentials using the native client's
normal runtime mechanism. Project configuration is trusted, as when running those clients directly.

## Release entries

Use these for exceptional standalone tools with an explicitly declared source. A name matching explicit inline metadata
(`format.custom`, `lint.custom`, etc.) replaces that row.

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
};
```

For debputy, which nix-tools packages locally, use the public package accessor instead of copying its version string:

```nix
let
  debputy = (nix-tools.lib.packagesFor pkgs).debputy;
in
nix-tools.lib.configure {
  inherit system;
  toolPkgs = pkgs;
  src = self;
  outdated.releases.debputy = {
    package = debputy;
    provider = "git";
    url = "https://salsa.debian.org/debian/debputy.git";
    tagPattern = ''(?:archive/)?debian/(?P<version>[0-9]+(?:\.[0-9]+)+)'';
  };
}
```

`packagesFor` uses the same package set passed as `toolPkgs`, and the Debian formatter and checker use this derivation
by default. If you override their `package` options, pass that same derivation to the release entry. The accessor only
exposes the packaged tool; it does not select an upstream release source or add an automatic report row.

Release discovery uses the pinned [nvchecker](https://nvchecker.readthedocs.io/en/latest/usage.html) CLI and its
documented JSON events. Explicit GitHub release entries use the repository's latest release by default; `tags = true`
requests tag discovery. The built-in Nix input provider always uses tags for version refs. Git and GitHub tags share a
numeric dotted-version policy with an optional `v`; there is no package-specific extraction rule. An explicit
`tagPattern` filters the native candidate list and extracts its named `version` group. There is no implicit fallback to
another source when a lookup fails.

Unreadable release versions, unmatched policies and unknown release-version ordering produce `skipped` rows with
reasons. Authentication, network, command and malformed-report failures remain errors. Skips do not claim that a package
is current. An installed version newer than the selected release is `ahead`.

Package registries are preferred when they publish the tool's versions. nvchecker's `npm`, `cratesio` and `pypi` plugins
query their public registries; this release inventory does not use project `.npmrc` or UV index configuration. The
separate native dependency providers retain those settings. `provider = "npm"` selects `latest`; `provider = "crates"`
selects a non-yanked stable version. Both compare through npm's maintained SemVer library. `project` identifies the
package (`@biomejs/biome` or `lychee`), so GitHub tag namespaces are irrelevant.

For runtime discovery, a release entry can supply `versionCommand = [ "nix" "--version" ];` and
`versionPattern = "nix .* (?P<version>[^ ]+)";` with `repo = "NixOS/nix"; tags = true;`. The command runs at report time
with inherited PATH/environment, disconnected stdin, the configured timeout, and a disposable repository copy as its
working directory. Without a pattern it must print only the version; with a pattern its entire trimmed stdout must match
and expose `version`. Empty/malformed output or command failure is an error. The same built-in upstream lookup then
compares that version. `versionCommand` replaces package-version discovery and cannot be combined with an explicit
`version`.

Custom checker/formatter declarations can own their metadata instead of repeating string-keyed release entries:

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

An `outdated` attribute set accepts the same fields as a release entry. `package` supplies the current version; a source
must be declared explicitly. No package name is mapped to an upstream source automatically. Metadata is not passed to
the checker or formatter and does not change formatting cache inputs.

For other static exceptions, use `outdated.skips."lint.local" = "Versioned with this repository";`. These entries
replace matching explicit rows, require a nonempty reason, and invoke no command or source copy. Releases, adapters and
skips must have distinct names. Skips cannot be combined with release settings.

## Command adapters and migration

Keep OMV constraints, Proton download scraping, application-selected container images and other domain rules in the
consumer. Package an adapter with the required runtime and opt it in:

```nix
outdated.adapters.application = {
  package = myOutdatedAdapter; # or exe = lib.getExe myOutdatedAdapter;
  args = [ "--policy" "application" ];
  runtimeInputs = [ pkgs.git ];
  root = ".";
  timeout = 60;
};
```

An adapter name matching an explicit inline entry replaces that row, just like a release entry. Use static skips for
repository-owned checkers; executable adapters are only needed for runtime policy. Unsupported native dependency sources
still need a replacement inventory adapter; adding an unrelated release entry does not silently clear their unknown
rows.

The runner calls `exe args... --root <disposable-copy>`, with that copy as the working directory and stdin disconnected.
The copy deliberately excludes Git metadata and installed environments. Adapters must exit **0** on a valid report,
including outdated findings, and emit exactly one JSON document on stdout:

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

Per-row allowed fields are `name`, `state`, `current`, `compatible`, `latest` and `detail`, all strings. `name` and
`state` are required. Unknown fields/states, empty reports, malformed JSON, timeout or nonzero process status fail. The
runner supplies provider/source and calculates the aggregate exit status. Adapters can return `unknown`, `blocked` or
`error` rows to describe partial results without discarding successful rows.

For an existing maintenance script, use file-backed providers for matching manifests and locks. Declare standalone tools
explicitly where needed and retain domain-specific sections as adapters. Keep runtime compatibility constraints in the
consumer.

Test the working checkout without changing the consumer pin:

```sh
nix run --override-input nix-tools path:/path/to/nix-tools --no-write-lock-file .#outdated -- --json
```

Online findings (exit 1) are expected on older pins; exit 2 always needs inspection. Compare component coverage and
source identities with the former report, then rerun the consumer's normal validation/coverage/flake checks. Never put
live smoke tests in a cacheable check derivation; this project's client tests use loopback registries and fixtures.

## Python development

The repository's `pyproject.toml` declares Python 3.14, runtime dependencies and the development group; `uv.lock` pins
their versions. `nix/python.nix` uses uv2nix to build the production runtime and development/test environments from that
lock. The CLI uses Tyro; Ty checks the outdated runtime and its tests. Consumer applications use this environment
automatically.

`nix develop` supplies the locked development interpreter and UV. Outside that shell, `uv sync --locked` creates a local
`.venv` for editing and tests. Update dependencies with `uv lock --upgrade`; `just check` verifies lock consistency,
installed versions, type checking and separation of runtime/development dependencies. Run `uv run --locked ty check` for
types and `uv run --locked python tests/outdated_test.py lib/outdated` for the Python contract tests.
