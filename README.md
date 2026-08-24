# nix-tools

`repofmt` (a treefmt wrapper) and `repochk` (a lint runner) as a configurable flake library. One pinned tool set, so
formatting moves when you bump it and not before.

## Use

```nix
{
  inputs.nix-tools.url = "github:kubijo/nix-tools";

  outputs =
    {
      self,
      nixpkgs,
      nix-tools,
    }:
    let
      system = "x86_64-linux";
      project = nix-tools.lib.configure {
        inherit system;
        src = self;
        nodejs = pkgs.nodejs_24;
        exclude = [ "vendor/**" ];
        format = {
          rust.exe = lib.getExe' toolchain "rustfmt";
          javascript = true;
        };
        lint.python = true;
        validate.steps = [
          "cargo clippy --all-targets -- -D warnings"
          {
            name = "tests";
            run = "cargo nextest run";
          }
        ];
      };
    in
    {
      formatter.${system} = project.formatter;
      checks.${system} = project.checks; # { formatting, linting }
      apps.${system} = project.apps; # { format, lint, validate }
      devShells.${system}.default = pkgs.mkShellNoCC { inherit (project) packages; };
    };
}
```

`configure` is the only entrypoint; the primitives behind it stay unexported, so a repo cannot end up with a formatter
and a checker that disagree about scope. A derivation comes back only where a flake output needs one — the checker and
the gate arrive as `apps`, runnable but not rebuildable into a check that drops what `checks` guarantees.

`apps.validate` is the local gate: formatter, checker, then `validate.steps`. Every step runs and the exit code is the
worst of them, so one pass reports every problem; `validate.failFast = true` stops at the first. It is the one piece
that does not null `PATH`, since its steps want the dev shell they were started from.

The pinned set supports `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`; other systems are rejected at evaluation.

## repofmt

| Toggle       | Tool         | Globs                          | Default |
| ------------ | ------------ | ------------------------------ | ------- |
| `nix`        | [`nixfmt`]   | `*.nix`                        | ✅ on   |
| `shell`      | [`shfmt`]    | `*.sh` `*.bash` `*.envrc`      | ✅ on   |
| `markdown`   | [`mdformat`] | `*.md` `*.markdown`            | ✅ on   |
| `toml`       | [`taplo`]    | `*.toml`                       | ✅ on   |
| `yaml`       | [`yamlfmt`]  | `*.yaml` `*.yml`               | ✅ on   |
| `json`       | [`biome`]    | `*.json` `*.jsonc`             | ✅ on   |
| `justfile`   | [`just`]     | `justfile` `*.just`            | ✅ on   |
| `rust`       | [`rustfmt`]  | `*.rs`                         | ❌ off  |
| `python`     | [`ruff`]     | `*.py` `*.pyi`                 | ❌ off  |
| `javascript` | [`biome`]    | `*.js` `*.mjs` `*.cjs` `*.jsx` | ❌ off  |
| `typescript` | [`biome`]    | `*.ts` `*.mts` `*.cts` `*.tsx` | ❌ off  |
| `css`        | [`biome`]    | `*.css`                        | ❌ off  |
| `html`       | [`biome`]    | `*.html` `*.htm`               | ❌ off  |
| `graphql`    | [`biome`]    | `*.graphql` `*.gql`            | ❌ off  |
| `scss`       | [`prettier`] | `*.scss` `*.sass`              | ❌ off  |
| `protobuf`   | [`buf`]      | `*.proto`                      | ❌ off  |
| `sql`        | [`sqlfluff`] | `*.sql`                        | ❌ off  |
| `po`         | [`msgcat`]   | `*.po` `*.pot`                 | ❌ off  |
| `svg`        | [`svgo`]     | `*.svg`                        | ❌ off  |
| `png`        | [`oxipng`]   | `*.png`                        | ❌ off  |
| `caddyfile`  | [`caddy`]    | `Caddyfile` `*.caddyfile`      | ❌ off  |

`javascript.organizeImports` and `typescript.organizeImports` opt into a Biome assist phase before formatting, using the
same pinned executable, config and scope. This mode rejects a wholesale `options` replacement.

`onUnmatched` defaults to `fatal`, so the first unclaimed file type fails the run rather than rotting unformatted.

Only enabled entries are evaluated, so an off toggle costs nothing — the defaults close over 455 MiB. `json` and
`markdown` account for 245 MiB of that through biome and mdformat, and stay on because `fatal` would otherwise fail any
repo holding a `README.md`.

**A formatter that runs on a language runtime takes yours, or refuses to build.** `rust` needs `rust.exe` or
`rust.package`, because nixpkgs' rustfmt drags in a 1.6 GiB toolchain beside the one you already pin. `scss` and `svg`
need a top-level `nodejs`, which both are rebuilt against, so enabling either or both lands exactly one node closure:

```nix
nix-tools.lib.configure {
  inherit system src;
  nodejs = pkgs.nodejs_24; # one runtime for every node tool
  format = {
    rust.exe = lib.getExe' toolchain "rustfmt";
    scss = true;
    svg = true;
  };
}
```

`project.packages` already carries that runtime alongside the tools, so a dev shell built from it cannot end up on a
second node.

## repochk

| Toggle       | Tool                          | Globs                                    | Default |
| ------------ | ----------------------------- | ---------------------------------------- | ------- |
| `nix`        | [`statix`] + [`deadnix`]      | `*.nix`                                  | ✅ on   |
| `shell`      | [`shellcheck`]                | `*.sh` `*.bash` `.envrc`                 | ✅ on   |
| `yaml`       | [`yamllint`]                  | `*.yaml` `*.yml`                         | ✅ on   |
| `workflows`  | [`actionlint`]                | `.github/workflows` `.forgejo/workflows` | ✅ on   |
| `python`     | [`ruff check`][`ruff`]        | `*.py` `*.pyi`                           | ❌ off  |
| `javascript` | [`biome lint`][`biome`]       | `*.js` `*.mjs` `*.cjs` `*.jsx`           | ❌ off  |
| `typescript` | [`biome lint`][`biome`]       | `*.ts` `*.mts` `*.cts` `*.tsx`           | ❌ off  |
| `links`      | [`lychee`]                    | `*.md` `*.markdown`                      | ❌ off  |
| `protobuf`   | [`buf lint`][`buf`]           | `*.proto`                                | ❌ off  |
| `sql`        | [`sqlfluff lint`][`sqlfluff`] | `*.sql`                                  | ❌ off  |
| `po`         | [`msgfmt`]                    | `*.po`                                   | ❌ off  |

Each shares the tool and config its formatter counterpart uses, so `ruff check` and `ruff format` cannot disagree about
line length. `sql` requires `configFile`, since SQLFluff cannot safely guess a dialect. `links` resolves on-disk targets
only — an unreachable host never fails it — and takes patterns matched against the link rather than the file holding it:

```nix
lint.links = {
  exclude = [ "CHANGELOG.md" ]; # files this checker skips
  ignoreLinks = [ "^https?://" "generated/.*" ]; # targets it will not resolve
};
```

An omitted `batch` inherits the built-in default; an explicit bool overrides it. Custom checkers still default to
`false`.

Like repofmt, repochk searches upward for the top-level `treeRootFile` before scanning, so subdirectory runs cover the
same repository.

## Options

Every toggle is a bool or an attrset of that entry's options, and the attrset is destructured strictly, so a misspelled
key throws rather than being ignored. Excludes come in three narrowing layers:

Formatter toggles accept `enable`, `exclude`, `includes`, `configFile`, `package`, `exe`, `options`, `extraOptions` and
`priority`; file checkers omit `options` and `priority`, and add `batch`. `links` also takes `ignoreLinks`.

```nix
nix-tools.lib.configure {
  inherit system src;
  exclude = [ "vendor/**" ]; # both tools
  unexclude = [ "Cargo.toml" ]; # drop one of the defaults
  format = {
    exclude = [ "generated/**" ]; # the formatter alone
    yaml = {
      configFile = ./yamlfmt.yml; # replaces the shipped default
      exclude = [ "helm/**" ]; # this language alone
    };
  };
  lint.exclude = [ "fixtures/**" ]; # the checker alone
}
```

`lib.conf` exposes the shipped configs and `lib.defaultExcludes` the default exclude list, so either can be extended
rather than restated.

`configFile` works for every tool that has one: taplo, yamlfmt, biome (`json`, `javascript`, `typescript`, `css`,
`html`, `graphql`), prettier, rustfmt, ruff, SQLFluff, svgo, buf and actionlint. nixfmt, shfmt, mdformat, just, msgcat,
msgfmt and oxipng take none, and say so at eval rather than dropping the setting. `caddy fmt` is the odd one out: its
`--config` names the file to format, not a style, so wiring it would format the wrong file.

A config is staged into the store under its own basename, since ruff picks its parser from that and would read a
`<hash>-pyproject.toml` as a flat `ruff.toml`. One consequence the library cannot paper over: ruff resolves `src`
relative to the config's own directory, so a repo passing its own ruff config needs
`[tool.ruff.lint.isort] known-first-party = [...]` or first-party detection silently stops working.

## Splicing in your own tools

A repo's homegrown tooling joins the runners rather than running beside them, so it inherits the excludes, the single
pass and the reporting instead of restating all three:

```nix
format.extraFormatters.stylelint = {
  command = ./stylelint-wrapper; # joins treefmt's run, so it shares the cache
  includes = [ "*.scss" ];
  cacheInputs = [ ./stylelint.config.mjs ./pnpm-lock.yaml ];
  priority = 1; # after the scss formatter
};

lint.extraCheckers.ruff = {
  command = lib.getExe (nix-tools.lib.toolPkgsFor system).ruff;
  options = [ "check" ];
  includes = [ "*.py" ];
  batch = true; # one invocation for every match, not one per file
};
```

`cacheInputs` is required, even when empty. The library makes the custom command, its argv and these config/manifest
paths visible to treefmt's cache key without passing cache-only paths to the command.

Use `nix-tools.lib.toolPkgsFor system` while constructing splices, rather than a dummy `configure` call or the
consumer's unrelated nixpkgs.

Naming a built-in overrides it rather than adding a second. Anything whole-project rather than per-file belongs at the
project-checker layer and runs once without a trigger glob or synthetic file arguments:

```nix
lint.extraProjectCheckers.codegen = {
  command = ./check-generated-bindings;
  options = [ "--locked" ];
  exclude = [ "fixtures/**" ];
};
```

Project checks join repochk's aggregate report. Composed excludes are available as JSON in `REPOCHK_EXCLUDES_JSON` for
commands that traverse files themselves.

The returned values remain ordinary attrsets and lists, so unrelated checks and apps can still be added at the output
level:

```nix
checks.${system} = project.checks // { my-integration = pkgs.runCommandLocal "…" { } "…"; };
apps.${system} = project.apps // { serve = { type = "app"; program = "…"; }; };
devShells.${system}.default = pkgs.mkShellNoCC { packages = project.packages ++ [ pkgs.cargo-deny ]; };
```

And a command that only needs to run in the local gate is a `validate.steps` entry.

## Exported checks

Both checks copy `src` into a sandbox and run the complete local tool configuration. `check.prepare` can materialize
store-backed state such as dependency trees after each copy:

```nix
project = nix-tools.lib.configure {
  inherit system src;
  check.prepare = "cp -r ${frontendDeps}/node_modules frontend/node_modules";
  check.runtimeInputs = [ pkgs.coreutils ];
};
```

There is no `localOnly` splice: if required state cannot be prepared hermetically, use the local apps or validate gate
and do not export `project.checks`.

## Two rules that bite

**`nixpkgs-pinned` is not meant to be `follows`-ed.** An attribute name is not a stable identity: `nixfmt` meant the
classic formatter until nixpkgs flipped the alias to the RFC-style rewrite, so a `follows` can restyle every `.nix` file
without ever erroring. The configs are version-bound too — `conf/biome.json` names biome 2.5.8's schema, and biome
rejects a key it does not know. Override per call site with `toolPkgs`.

**Configs are passed by store path, in the tool's `options`.** treefmt hashes a formatter's name, joined options,
priority and its executable's size and mtime — never a config's contents. A config reached any other way, including one
baked into a wrapper script, cannot invalidate the cache, so editing it becomes a silent no-op.

[`actionlint`]: https://rhysd.github.io/actionlint/
[`biome`]: https://biomejs.dev/
[`buf`]: https://buf.build
[`caddy`]: https://caddyserver.com
[`deadnix`]: https://github.com/astro/deadnix
[`just`]: https://github.com/casey/just
[`lychee`]: https://github.com/lycheeverse/lychee
[`mdformat`]: https://mdformat.rtfd.io/
[`msgcat`]: https://www.gnu.org/software/gettext/manual/html_node/msgcat-Invocation.html
[`msgfmt`]: https://www.gnu.org/software/gettext/manual/html_node/msgfmt-Invocation.html
[`nixfmt`]: https://github.com/NixOS/nixfmt
[`oxipng`]: https://github.com/oxipng/oxipng
[`prettier`]: https://prettier.io/
[`ruff`]: https://github.com/astral-sh/ruff
[`rustfmt`]: https://github.com/rust-lang/rustfmt
[`shellcheck`]: https://www.shellcheck.net/
[`shfmt`]: https://github.com/mvdan/sh
[`sqlfluff`]: https://www.sqlfluff.com/
[`statix`]: https://github.com/molybdenumsoftware/statix
[`svgo`]: https://github.com/svg/svgo
[`taplo`]: https://taplo.tamasfe.dev
[`yamlfmt`]: https://github.com/google/yamlfmt
[`yamllint`]: https://github.com/adrienverge/yamllint
