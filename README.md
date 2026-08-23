# nix-tools

`repofmt` (a treefmt wrapper) and `repochk` (a lint runner) as a configurable flake library. One pinned tool set, so
every repo using it formats identically.

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

`configure` is the only entrypoint. The primitives behind it stay unexported deliberately — each carries part of a
guarantee, and reaching one directly is how a repo ends up with a formatter and a checker that disagree about scope, or
a tool built against a runtime nothing else uses.

`configure` returns only what a flake output needs the derivation for. The checker and the gate come back as `apps`
instead, so they can be run but not rebuilt into a check that quietly drops what `checks` guarantees.

`apps.validate` is the local gate: formatter, checker, then `validate.steps`. Every step runs and the exit code is the
worst of them, so one pass reports every problem; `validate.failFast = true` stops at the first. It is the one piece
that does not null `PATH`, since its steps want the dev shell they were started from.

## repofmt

| Toggle       | Tool     | Globs                          | Default |
| ------------ | -------- | ------------------------------ | ------- |
| `nix`        | nixfmt   | `*.nix`                        | on      |
| `shell`      | shfmt    | `*.sh` `*.bash` `*.envrc`      | on      |
| `markdown`   | mdformat | `*.md` `*.markdown`            | on      |
| `toml`       | taplo    | `*.toml`                       | on      |
| `yaml`       | yamlfmt  | `*.yaml` `*.yml`               | on      |
| `json`       | biome    | `*.json` `*.jsonc`             | on      |
| `justfile`   | just     | `justfile` `*.just`            | on      |
| `rust`       | rustfmt  | `*.rs`                         | off     |
| `python`     | ruff     | `*.py` `*.pyi`                 | off     |
| `javascript` | biome    | `*.js` `*.mjs` `*.cjs` `*.jsx` | off     |
| `typescript` | biome    | `*.ts` `*.mts` `*.cts` `*.tsx` | off     |
| `css`        | biome    | `*.css`                        | off     |
| `html`       | biome    | `*.html` `*.htm`               | off     |
| `graphql`    | biome    | `*.graphql` `*.gql`            | off     |
| `scss`       | prettier | `*.scss` `*.sass`              | off     |
| `protobuf`   | buf      | `*.proto`                      | off     |
| `sql`        | sqlfluff | `*.sql`                        | off     |
| `po`         | msgcat   | `*.po` `*.pot`                 | off     |
| `svg`        | svgo     | `*.svg`                        | off     |
| `png`        | oxipng   | `*.png`                        | off     |
| `caddyfile`  | caddy    | `Caddyfile` `*.caddyfile`      | off     |

Language-specific formatters are off by default. `onUnmatched` defaults to `fatal`, so the first unclaimed file type
fails the run rather than rotting unformatted.

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
second node. That is the reason to take the list rather than name the tools yourself.

## repochk

| Toggle       | Tool             | Globs                                    | Default |
| ------------ | ---------------- | ---------------------------------------- | ------- |
| `nix`        | statix + deadnix | `*.nix`                                  | on      |
| `shell`      | shellcheck       | `*.sh` `*.bash` `.envrc`                 | on      |
| `yaml`       | yamllint         | `*.yaml` `*.yml`                         | on      |
| `workflows`  | actionlint       | `.github/workflows` `.forgejo/workflows` | on      |
| `python`     | ruff check       | `*.py` `*.pyi`                           | off     |
| `javascript` | biome lint       | `*.js` `*.mjs` `*.cjs` `*.jsx`           | off     |
| `typescript` | biome lint       | `*.ts` `*.mts` `*.cts` `*.tsx`           | off     |
| `links`      | lychee           | `*.md` `*.markdown`                      | off     |

Each shares the tool and config its formatter counterpart uses, so `ruff check` and `ruff format` cannot disagree about
line length. `links` resolves on-disk targets only — an unreachable host never fails it — and takes patterns matched
against the link rather than the file holding it:

```nix
lint.links = {
  exclude = [ "CHANGELOG.md" ]; # files this checker skips
  ignoreLinks = [ "^https?://" "generated/.*" ]; # targets it will not resolve
};
```

## Options

Every toggle is a bool or an attrset of that entry's options, and the attrset is destructured strictly, so a misspelled
key throws rather than being ignored. Excludes come in three narrowing layers:

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
    extraFormatters.stylelint = {
      command = ./stylelint-wrapper;
      includes = [ "*.scss" ];
      priority = 1;
    };
  };
  lint.exclude = [ "fixtures/**" ]; # the checker alone
}
```

`lib.conf` exposes the shipped configs and `lib.defaultExcludes` the default exclude list, so either can be extended
rather than restated.

`configFile` works for every tool that has one: taplo, yamlfmt, biome (`json`, `javascript`, `typescript`, `css`,
`html`, `graphql`), prettier, rustfmt, ruff, sqlfluff, svgo and buf. nixfmt, shfmt, mdformat, just, msgcat and oxipng
take none, and say so at eval rather than dropping the setting. `caddy fmt` is the odd one out: its `--config` names the
file to format, not a style, so wiring it would format the wrong file.

## Splicing in your own tools

A repo's homegrown tooling joins the runners rather than running beside them, so it inherits the excludes, the single
pass and the reporting instead of restating all three:

```nix
format.extraFormatters.stylelint = {
  command = ./stylelint-wrapper; # joins treefmt's run, so it shares the cache
  includes = [ "*.scss" ];
  priority = 1; # after the scss formatter
};

lint.extraCheckers.ruff = {
  command = "${pkgs.ruff}/bin/ruff"; # PATH is nulled — store path, or `lint.extraRuntimeInputs`
  options = [ "check" ];
  includes = [ "*.py" ];
  batch = true; # one invocation for every match, not one per file
};
```

Naming a built-in overrides it rather than adding a second. Anything whole-project rather than per-file belongs at the
outputs level, where the returned values are ordinary attrsets and lists:

```nix
checks.${system} = project.checks // { my-integration = pkgs.runCommandLocal "…" { } "…"; };
apps.${system} = project.apps // { serve = { type = "app"; program = "…"; }; };
devShells.${system}.default = pkgs.mkShellNoCC { packages = project.packages ++ [ pkgs.cargo-deny ]; };
```

And a command that only needs to run in the local gate is a `validate.steps` entry, which costs nothing to add.

## Two rules that bite

**`nixpkgs-pinned` is not meant to be `follows`-ed.** Identical tool versions across repos is the point; a `follows`
resolves the tools against your nixpkgs instead. Override per call site with `toolPkgs`.

**Configs are passed by store path, in the tool's `options`.** treefmt hashes a formatter's name, joined options,
priority and its executable's size and mtime — never a config's contents. A config reached any other way, including one
baked into a wrapper script, cannot invalidate the cache, so editing it becomes a silent no-op.

## Contributing

`just` lists the recipes; `just validate` runs everything CI gates on. Goldens are regenerated with `just test update`
and the diff is meant to be read.

`just release minor` bumps the last tag, dates the `## [Unreleased]` section, commits it and cuts an annotated tag
carrying those notes as its message. It stops there — pushing is `git push --follow-tags`.
