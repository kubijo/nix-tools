# nix-tools

Pinned formatters and linters for Nix flakes. `repofmt` wraps treefmt; `repochk` runs file and project checks.

## Use

```nix
{
  inputs.nix-tools.url = "github:kubijo/nix-tools";

  outputs =
    { self, nix-tools }:
    let
      system = "x86_64-linux";
      project = nix-tools.lib.configure {
        inherit system;
        src = self;
        format.python = true;
        lint.python = true;
      };
    in
    {
      formatter.${system} = project.formatter;
      checks.${system} = project.checks;
      apps.${system} = project.apps;
    };
}
```

```sh
nix fmt                              # format
nix run .#lint                       # lint
nix run .#validate                   # format, lint, then configured project steps
nix flake check                      # hermetic checks for CI
nix run .#coverage -- --json --check # audit tracked-file coverage
```

Supports `x86_64-linux`, `aarch64-linux` and `aarch64-darwin`. Add `project.packages` to your dev shell if needed.

## Configure

Formatting defaults: Nix, shell, Markdown, TOML, YAML, JSON and justfiles. Lint defaults: Nix, shell, YAML and CI
workflows. Other tools are opt-in; see the [tool tables](docs/reference.md#repofmt). Unmatched formatter files are fatal
by default.

Each language has independent `format` and `lint` entries. Use a boolean or an options attrset; an attrset enables the
entry unless `enable = false`.

```nix
format.php = true;
lint.php.extraOptions = [ "--semantics" ];

format.nunjucks.includes = [ "*.html.njk" ];
lint.nunjucks.includes = [ "*.html.njk" ];

format.yaml.configFile = ./yamlfmt.yml;
exclude = [ "vendor/**" ];
validate.steps = [ { name = "tests"; run = "pytest"; } ];
```

Formatter includes support path globs; lint includes match basenames. HTML template entries require explicit, nonempty
includes. Use whitespace formatting for non-HTML templates and retain project rendering/validation checks.

Keep the tool pin independent of your nixpkgs. Rust formatting requires your toolchain; SCSS and SVG formatting require
your `nodejs`. Details and overrides are in the reference.

Opt into `outdated = true` for an online Nix-input report via `nix run .#outdated`. Package-manager and GitHub Actions
inventories are separate opt-ins; standalone tool releases require explicit sources. See
[outdated reports](docs/outdated.md).

## Reference

- [Tools, options and exclusions](docs/reference.md)
- [PHP and Debian packaging](docs/reference.md#php-and-debian-packaging)
- [Python dependency checks](docs/reference.md#python-dependency-checks)
- [HTML templates](docs/reference.md#html-templates) · [Salt and whitespace](docs/reference.md#saltjinja-and-whitespace)
- [Custom tools](docs/reference.md#splicing-in-your-own-tools) · [Project checks](docs/reference.md#exported-checks)
- [Grit](docs/reference.md#grit) · [ast-grep](docs/reference.md#ast-grep)
- [Coverage audit](docs/reference.md#coverage-audit) · [Pinning and cache](docs/reference.md#pinning-and-cache)
- [Changelog](CHANGELOG.md)
