# Configuration reference

[Quick start](../README.md)

- [Formatter tools](#repofmt) · [Lint tools](#repochk)
- [Options](#options) · [Custom tools](#splicing-in-your-own-tools) · [Exported checks](#exported-checks)
- [PHP and Debian](#php-and-debian-packaging) · [HTML templates](#html-templates) ·
  [Salt and whitespace](#saltjinja-and-whitespace)
- [Coverage](#coverage-audit) · [Grit](#grit) · [ast-grep](#ast-grep) · [Pinning and cache](#pinning-and-cache)

## repofmt

| Toggle       | Tool                     | Globs                          | Default |
| ------------ | ------------------------ | ------------------------------ | ------- |
| `nix`        | [`nixfmt`]               | `*.nix`                        | ✅ on   |
| `shell`      | [`shfmt`]                | `*.sh` `*.bash` `*.envrc`      | ✅ on   |
| `markdown`   | [`mdformat`]             | `*.md` `*.markdown`            | ✅ on   |
| `toml`       | [`taplo`]                | `*.toml`                       | ✅ on   |
| `yaml`       | [`yamlfmt`]              | `*.yaml` `*.yml`               | ✅ on   |
| `json`       | [`biome`]                | `*.json` `*.jsonc`             | ✅ on   |
| `justfile`   | [`just`]                 | `justfile` `*.just`            | ✅ on   |
| `rust`       | [`rustfmt`]              | `*.rs`                         | ❌ off  |
| `python`     | [`ruff`]                 | `*.py` `*.pyi`                 | ❌ off  |
| `php`        | [`Mago`]                 | `*.php` `*.inc`                | ❌ off  |
| `debian`     | [`debputy`]              | supported Debian metadata¹     | ❌ off  |
| `whitespace` | [`editorconfig-checker`] | `*.sls` `*.j2` `*.jinja`       | ❌ off  |
| `javascript` | [`biome`]                | `*.js` `*.mjs` `*.cjs` `*.jsx` | ❌ off  |
| `typescript` | [`biome`]                | `*.ts` `*.mts` `*.cts` `*.tsx` | ❌ off  |
| `css`        | [`biome`]                | `*.css`                        | ❌ off  |
| `html`       | [`biome`]                | `*.html` `*.htm`               | ❌ off  |
| `graphql`    | [`biome`]                | `*.graphql` `*.gql`            | ❌ off  |
| `scss`       | [`prettier`]             | `*.scss` `*.sass`              | ❌ off  |
| `protobuf`   | [`buf`]                  | `*.proto`                      | ❌ off  |
| `sql`        | [`sqlfluff`]             | `*.sql`                        | ❌ off  |
| `po`         | [`msgcat`]               | `*.po` `*.pot`                 | ❌ off  |
| `grit`       | [`GritQL`]               | `*.grit`                       | ❌ off  |
| `xml`        | [`xmllint`]              | `*.xml` `*.gpx`                | ❌ off  |
| `svg`        | [`svgo`]                 | `*.svg`                        | ❌ off  |
| `png`        | [`oxipng`]               | `*.png`                        | ❌ off  |
| `caddyfile`  | [`caddy`]                | `Caddyfile` `*.caddyfile`      | ❌ off  |

`javascript.organizeImports` and `typescript.organizeImports` opt into a Biome assist phase before formatting, using the
same pinned executable, config and scope. This mode rejects a wholesale `options` replacement.

The Grit formatter uses the GritQL revision pinned through `nix-gritql`. Because `grit format` is directory-scoped,
repofmt formats its selected `*.grit` files in isolation and replaces the originals only after the complete batch
succeeds. Its operation is fixed, so `format.grit` deliberately does not accept `configFile`, `options`, or
`extraOptions`. Markdown pattern files remain ordinary Markdown inputs.

`onUnmatched` defaults to `fatal`, so the first unclaimed file type fails the run rather than rotting unformatted.

Disabled tools are not evaluated.

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
| `php`        | [`Mago`]                      | `*.php` `*.inc`                          | ❌ off  |
| `debian`     | [`debputy`]                   | project-scoped Debian metadata¹          | ❌ off  |
| `salt`       | [`salt-lint`]                 | `*.sls` `*.j2` `*.jinja`                 | ❌ off  |
| `whitespace` | [`editorconfig-checker`]      | `*.sls` `*.j2` `*.jinja`                 | ❌ off  |
| `javascript` | [`biome lint`][`biome`]       | `*.js` `*.mjs` `*.cjs` `*.jsx`           | ❌ off  |
| `typescript` | [`biome lint`][`biome`]       | `*.ts` `*.mts` `*.cts` `*.tsx`           | ❌ off  |
| `links`      | [`lychee`]                    | `*.md` `*.markdown`                      | ❌ off  |
| `protobuf`   | [`buf lint`][`buf`]           | `*.proto`                                | ❌ off  |
| `sql`        | [`sqlfluff lint`][`sqlfluff`] | `*.sql`                                  | ❌ off  |
| `po`         | [`msgfmt`]                    | `*.po`                                   | ❌ off  |
| `xml`        | [`xmllint`]                   | `*.xml` `*.gpx`                          | ❌ off  |

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
`false`. File checkers receive `/dev/null` on stdin by default. Set `stdin = "inherit"` to deliberately read the
caller's input. Discovery completes before invocation, and any discovery failure fails the runner without processing
partial results, in both batch and per-file modes. Traversal diagnostics (including unreadable directories) are fatal
even when fd exits zero; the coverage audit and Debian discovery enforce the same rule. Explicitly excluded directories
remain outside discovery scope.

Lint `includes` are basename globs: use `"omv-protondrive"`, not `"usr/sbin/omv-protondrive"`. Slash-containing patterns
fail at evaluation with an actionable error. Custom checkers can use `searchPaths = [ "usr/sbin" ];` with basename
includes to narrow scope. Formatter includes retain treefmt's path-glob support. Each lint glob is passed unchanged to
fd, including literal commas and brace alternatives. Overlapping includes check each matching path once per search root.
Missing search directories are optional. Inaccessible parents, symlink loops, and non-directory search roots fail
discovery in both lint and coverage; they are not treated as missing directories.

Like repofmt, repochk searches upward for the top-level `treeRootFile` before scanning, so subdirectory runs cover the
same repository.

## PHP and Debian packaging

```nix
format.php = true;
lint.php = {
  # Syntax and semantic sanity checks, without coding-style or complexity rules.
  extraOptions = [ "--semantics" ];
};
format.debian = true;
lint.debian = true;
```

PHP uses the pinned Mago executable for both formatting and linting. Plain `lint.php = true` runs Mago's normal rules;
`--semantics` selects its lightweight validation mode. Neither mode runs whole-program type analysis or needs PHP,
Composer, or application dependencies. `.inc` is included for consumers such as OpenMediaVault plugins. `format.php` and
`lint.php` accept the standard file-tool options, including `configFile`, `package`, `exe`, and `extraOptions`.
Formatting uses Mago's default style. Both commands explicitly receive `lib.conf.mago` unless overridden; a
project-local `mago.toml` is therefore used only when passed as `configFile`.

¹ Debian formatting covers `debian/control`, `debian/copyright` (DEP-5), `debian/tests/control`, and `debian/watch`
(deb822 version 5). It uses debputy's `black` style; override that through `extraOptions` or replace the formatter's
`options`. Legacy watch syntax is unsupported by this formatter. Maintainer scripts remain shell inputs. Files such as
`debian/rules`, `debian/changelog`, `debian/source/format`, `*.install`, and `*.triggers` need formatting exclusions
unless another formatter claims them.

Debian linting runs once from the repository root and checks debputy's supported files: control, changelog, copyright,
rules, tests/control, patches/series, upstream/metadata, watch, and debputy.manifest. Manifest checking is partial;
debputy reports when its separate `check-manifest` command would be needed. Packaging must live in the root `debian/`
directory. A repository with no selected packaging files is a no-op; selected files require `debian/control` as package
context. Excluded control files can still supply context, but their own diagnostics are suppressed.

`lint.debian` accepts `enable`, `exclude`, `configFile`, `package`, `exe`, and `extraOptions`. It has no `includes` or
`batch` option because it is a project checker. Global and phase exclusions compose with its exclusions, including
diagnostics attributed to related files. `extraProjectCheckers.debian` replaces the built-in project checker. Linting
reports severe issues through a failing exit status, never fixes files, and leaves spellchecking off by default.
Built-package checks such as Lintian belong after a consumer's package build.

The locally packaged debputy includes a `debputy-nix-tools` adapter that limits upstream's directory-wide operations to
the runner's scope. Package overrides must provide that executable; `exe` overrides must implement the same contract:
`reformat [options] -- files...`, `lint [options]`, or `coverage` (JSON array of selected lint paths), with an optional
`--config PATH`. Lint selection uses the composed `REPOCHK_EXCLUDES_JSON`; formatting accepts only the supported
root-relative paths. The upstream `debputy` executable alone does not implement this contract.

Both Debian toggles accept a native debputy YAML `configFile`, defaulting to `lib.conf.debputy`. The adapter isolates
user configuration and loads only that file. This config controls debputy's diagnostics settings; formatting style is
selected through the command options. Tool and configuration paths remain visible to treefmt's cache. Debian formatting
rejects malformed deb822 before rewriting files and preserves file modes.

## HTML templates

`format` and `lint` independently expose `nunjucks`, `jinja`, `django`, `handlebars`, `liquid`, `golang`, `angular`,
`tera` and `askama`, sharing the pinned [`djLint`] package and default policy. All are off by default. Enabled entries
require nonempty `includes` selecting **HTML** templates; there are no fallback globs.

```nix
let
  policy = {
    configFile = ./djlint.toml;
  };
in
{
  format.nunjucks = policy // {
    includes = [ "*.html.njk" ];
  };
  lint.nunjucks = policy // {
    includes = [ "*.html.njk" ];
  };
  format.jinja = policy // {
    includes = [ "*.html.j2" ];
  };
  lint.jinja = false;
}
```

Omitting `configFile` uses `lib.conf.djlint`: four-space indentation, 120-column lines and registration of Nunjucks'
`switch` block. Custom policies using that block should include `custom_blocks = ["switch"]`. Config accepts native
TOML, `[tool.djlint]` in `pyproject.toml`, or JSON `.djlintrc`, supplied as a Nix path or derivation output. Each entry
overrides the policy's profile. The adapter ignores ambient configs; runner-level file selection still applies. Tool,
adapter, profile, policy and style flags participate in the formatter cache key.

The Nunjucks profile always disables native set/function expression formatting: djLint's JSON/Python literal rewrites
can change Nunjucks string values. This also applies with a custom policy.

Standard options apply, except wholesale formatter `options` replacement. `extraOptions` accepts style/rule flags such
as `[ "--indent" "2" ]`; policy `per-file-ignores` regexes match repository-relative paths. Native selection, pragma,
mode, profile, external config/rule and `--warn` switches are rejected. Unsupported config keys fail explicitly.

djLint provides HTML/template style lint, not full template parsing or rendering. Keep rendering, escaping and
application validation in project checks. Salt, JSON and systemd templates should retain whitespace formatting and their
existing semantic checks. When adopting djLint, remove its patterns from overlapping formatters; keep any required
whitespace lint. `format.html` continues to use Biome.

## Salt/Jinja and whitespace

```nix
format.whitespace = { configFile = ./.editorconfig; };
lint.whitespace = { configFile = ./.editorconfig; };
lint.salt = true;

# Keep the consumer's strict rendering and output validation.
lint.extraProjectCheckers.templates = {
  command = "${pythonEnv}/bin/python";
  options = [ "scripts/check_templates.py" ];
};
```

`lint.salt` uses salt-lint's native rules for `*.sls`, `*.j2`, and `*.jinja`. The shipped `lib.conf.salt-lint` policy
exempts Jinja extensions from rule 205 (the `.sls` extension requirement); it retains that rule for Salt states.
`configFile` accepts a native salt-lint YAML policy, and `extraOptions` accepts native flags such as `[ "-x" "206" ]`.
An ambient `.salt-lint` is ignored. Both batch and per-file execution disconnect stdin unless explicitly opted in.

`format.whitespace` is **whitespace formatting**, using editorconfig-checker's `--fix`. It normalizes trailing
whitespace, line endings and final newlines according to the supplied policy. It does not reindent templates, format
HTML, or change Jinja control blocks. `lint.whitespace` checks the same policy, including any additional EditorConfig
rules the native checker supports. Neither validates Salt semantics, rendered YAML/JSON, or systemd units. Keep those
checks, OMV schemas and runtime tests in the consumer.

For these two toggles, `configFile` is an **EditorConfig file**, not editorconfig-checker's JSON configuration. Supply a
Nix path such as `./.editorconfig` or a derivation output. Plain runtime path strings are rejected so policy changes
cannot bypass the formatter cache. The default `lib.conf.editorconfig` specifies UTF-8, LF, a final newline and no
trailing whitespace, without indentation or line-length rules. A supplied policy applies its sections to
repository-relative paths. Ambient parent and nested `.editorconfig` files are ignored; put overrides in sections of the
explicit policy. The adapter resolves each file's properties and runs the native checker on an isolated copy. A
successful fix updates bytes only when changed and preserves file permissions. Native checks that `--fix` cannot repair
still fail. `package`/`exe` replace the native editorconfig-checker executable; `extraOptions` passes native options
except `--config` (use `configFile`) and `--exclude`/`-exclude` (use the tool's `exclude` option). Native exclusion
flags would match temporary filenames rather than repository paths and are rejected, including `=value` forms. Check
mode also rejects `--fix`/`-fix`, including their `=value` forms; use `format.whitespace` for fixes. Tool, adapter and
policy paths participate in the formatter cache key.

Defaults select only the three template extensions. Use `includes` to add plain configuration and unsupported Debian
text files, keeping native debputy and shell formatting for the files they own. For example:

```nix
format.whitespace = {
  configFile = ./.editorconfig;
  includes = [ "*.sls" "*.j2" "*.jinja" "*.conf" "debian/*" "debian/source/format" ];
  exclude = [ "debian/control" "debian/copyright" "debian/tests/control" "debian/watch"
    "debian/*.postinst" "debian/*.postrm" "debian/*.preinst" "debian/*.prerm" ];
};
# Lint patterns use basenames. includes = [ "*" ] can retain a previous whole-project
# whitespace check; exclusions must preserve the same consumer policy.
lint.whitespace = {
  configFile = ./.editorconfig;
  includes = [ "*" ];
  exclude = [ "LICENSE" ];
};
```

To replace a custom whitespace formatter and whole-project whitespace checker without reducing their scope, copy all
existing includes/excludes into these options, account for the shared default exclusions, and compare coverage. Use
top-level `unexclude = [ ".editorconfig" ];` if the policy file itself should be checked and formatted, then include
`.editorconfig` in the formatter. Explicitly unexclude generated metadata only if the old whitespace check covered it.
Remove the custom salt-lint stdin wrapper after enabling `lint.salt`; keep basename lint patterns. Retain existing
`php -l`, strict template rendering and project tests alongside Mago and debputy.

## Coverage audit

`nix run .#coverage` maps **tracked files** to the resolved formatters, file linters and declared project checks.
`--json` emits the complete versioned report; `--check` fails on uncovered required stages, missing source files,
untracked files or project checks with undeclared scope. This app audits selection and runs no formatter, linter or
project test. It does not replace `validate` or `flake check` and is not automatically added to either gate.

```nix
coverage = {
  required = [ "format" "lint" ]; # optionally add "tests"
  checkerKinds.php-syntax = "syntax"; # classify an existing custom file checker
  projectChecks = {
    "lint:templates" = {
      includes = [ "*.sls" "*.j2" "*.jinja" ];
      kind = "semantic";
      description = "Strict rendering followed by YAML/JSON/systemd validation";
    };
    "validate:tests" = {
      includes = [ "*.py" "usr/sbin/omv-protondrive" ];
      kind = "test";
      description = "Consumer unit and runtime tests";
    };
  };
  exceptions = [
    { includes = [ "LICENSE" ]; reason = "Verbatim upstream license text"; }
    { includes = [ "*.lock" ]; reason = "Generated dependency locks validated by the package manager"; }
    { includes = [ "README.md" ]; stages = [ "lint" "tests" ]; reason = "Prose; Markdown formatting suffices"; }
  ];
};
```

Project scope is explicitly **declared**, not inferred from arbitrary commands. Keys reference configured
`lint.extraProjectCheckers` as `lint:<name>`, structural lint step names as `lint:<step name>`, or named
`validate.steps` as `validate:<name>`. Built-in debputy selection comes directly from its diagnostic registry. Custom
file checkers have kind `unspecified` until classified through `checkerKinds`. Kinds are `semantic`, `syntax`,
`whitespace`, `test`, and `unspecified`; only `semantic`/`syntax` satisfy the lint stage, and only `test` satisfies the
tests stage. Whitespace formatting satisfies the formatting stage while remaining clearly labeled; whitespace lint never
satisfies semantic/syntax lint coverage.

Declaration and exception scopes use Git glob pathspecs: basename patterns match at any depth; slash-containing patterns
match relative to the repository root. Both accept `exclude`. Exception reasons must be nonempty; exceptions explain
audit gaps without changing tool selection or weakening fatal unmatched-file handling. Default exclusions such as
generated lock files still appear and need an explicit reason when auditing them.

The report uses treefmt itself to record formatter selections, and the same fd arguments as repochk for lint selection.
It lists ignored outputs separately, identifies untracked files omitted by Git flakes, and compares paths with the
configured `src` snapshot (`in_flake_source`). Run the report from the Git checkout with `treeRootFile` at its root. A
`path:` source can include untracked files; this does not make them tracked or safe to omit from a later Git-based
invocation. The audit describes selection, not test execution or line coverage.

## Grit

Structural checks and their explicit codemod counterparts use named `lint.grit` profiles. `patterns` is an existing
directory containing only subdirectories and regular Markdown pattern files. Each filename stem is a stable Grit name,
must match `^[a-z][a-z0-9_]*$`, and must be unique across the collection. A document's first heading is its title, its
first paragraph is the violation explanation, its single `grit` fence is the executable pattern, and the following code
fences are executable samples: an input/output pair asserts a rewrite, while an unpaired input asserts no match.
Omitting `=>` makes a match-only policy; including it makes a fixable policy or codemod. The generated Grit
configuration remains an internal store artifact.

`paths` contains existing directories relative to the consumer source root. It bounds traversal; `exclude` contains
root-relative globs that are pruned during that traversal:

```nix
lint.grit = {
  profiles = {
    policy = {
      patterns = ./.config/grit/policy;
      paths = [ "crates" ];
    };
    rename-widget-api = {
      patterns = ./.config/grit/codemods/rename-widget-api;
      paths = [ "widgets" ];
      gate = false;
    };
  };
};
```

Every profile requires `patterns` and at least one target in `paths`, and exports `grit-NAME-check`, `grit-NAME-apply`,
and `grit-NAME-test` apps. Enabling Grit also exports the aggregate `grit-check` app. Profiles are gated by default and
add a `checks.grit-NAME` entry; `gate = false` keeps the repository scan out of `flake check`, `lint`, and `validate`.
Pattern tests are always read-only and therefore export a `checks.grit-NAME-test` entry and join the normal lint and
validation apps even for codemod-only profiles. Gated scans join those local gates through the same aggregate app.

Only an apply app rewrites files; apply apps never join formatting, linting, validation, or checks. Selection ignores
ambient Git and user ignore files, walks only the configured directories, deduplicates overlapping roots, and composes
repository-wide and lint exclusions with each profile's exclusions. Grit apps accept no runtime arguments, preserving
the profile's declared behavior and scope.

A top-level `package` replaces the Grit executable for every profile, while `toolPkgs` supplies its runner dependencies.
`gritArgs.common` accepts `--log-level`; `.check` and `.apply` additionally accept `--verbose`. Check output retains
Grit's file, location, match-or-rewrite kind, Markdown explanation, and pattern name.

Use `nix run .#grit-check` for interactive diagnostics. It discovers every profile, tests every pattern collection,
scans every gated profile even after an earlier failure, prefixes output with the profile and operation, and reports one
aggregate result without building the failing check derivations. Successful operations collapse to one line; a failure
replays that operation's complete nonblank output exactly once. On a terminal, attribution is dimmed, passes are green,
and Grit's diagnostic colors are retained. `NO_COLOR` suppresses all styling; redirected output remains plain unless the
caller explicitly forces color. Use the individual checks or `nix flake check` for CI, where independently cached
derivations are the intended interface. The aggregate exits `1` for policy or pattern-test failures and `2` when it
observes a runner or infrastructure failure.

## ast-grep

ast-grep uses the same named-profile model. `configFile` is relative to the consumer source root; paths referenced by
the config, including `ruleDirs`, remain relative to the config file itself:

```nix
lint.ast-grep = {
  profiles.policy = {
    configFile = ".config/ast-grep/sgconfig.yml";
    paths = [ "crates" "web app" ];
  };
};
```

The `policy` profile exports the read-only `checks.ast-grep-policy` check and the explicit `apps.ast-grep-policy-check`
and `apps.ast-grep-policy-apply` apps. The apps accept no runtime arguments: their configuration and scope come only
from the profile. Check mode forces findings to error severity; only the apply app supplies `--update-all`. Profiles are
gated by default; `gate = false` retains both apps without adding a check. Neither app joins the default `format` app.
Gated check apps join `lint` and `validate`; apply apps never join any gate. Exported checks stay separate, so
`flake check` scans each gated profile once. Repository-wide and lint exclusions compose with each profile's exclusions.
A top-level `package` replaces the shared ast-grep executable; otherwise the selected `toolPkgs.ast-grep` is used.

## Options

Every toggle is a bool or an attrset of that entry's options, and the attrset is destructured strictly, so a misspelled
key throws rather than being ignored. Excludes come in three narrowing layers:

Most formatter toggles accept `enable`, `exclude`, `includes`, `configFile`, `package`, `exe`, `options`, `extraOptions`
and `priority`; the fixed-operation Grit formatter omits the three argument/configuration fields. File checkers omit
`options` and `priority`, and add `batch` and `stdin`. `links` also takes `ignoreLinks`. `grit` and `ast-grep` each take
`package` plus a strict `profiles` attrset. Grit profiles contain `patterns`, `paths`, `exclude`, `gritArgs`, and
`gate`; ast-grep profiles contain `configFile`, `paths`, `exclude`, and `gate`.

```nix
nix-tools.lib.configure {
  inherit system src;
  exclude = [ "vendor/**" ]; # every formatter, checker, and structural profile
  unexclude = [ "Cargo.toml" ]; # drop one of the defaults
  format = {
    exclude = [ "generated/**" ]; # the formatter alone
    yaml = {
      configFile = ./yamlfmt.yml; # replaces the shipped default
      exclude = [ "helm/**" ]; # this language alone
    };
  };
  lint.exclude = [ "fixtures/**" ]; # the checker and structural profiles
}
```

XML and GPX use the same opt-in scope in both runners:

```nix
format.xml = true;
lint.xml = true;
```

The checker verifies well-formed XML and namespace usage; it does not perform DTD, XML Schema or GPX schema validation.

`lib.conf` exposes the shipped configs and `lib.defaultExcludes` the default exclude list, so either can be extended
rather than restated.

`configFile` works for every tool that has one: taplo, yamlfmt, biome (`json`, `javascript`, `typescript`, `css`,
`html`, `graphql`), prettier, rustfmt, ruff, Mago, debputy, salt-lint, whitespace, djLint, SQLFluff, svgo, buf and
actionlint. nixfmt, shfmt, mdformat, just, msgcat, msgfmt, xmllint and oxipng take none, and say so at eval rather than
dropping the setting. `caddy fmt` is the odd one out: its `--config` names the file to format, not a style, so wiring it
would format the wrong file.

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

`validate` runs formatting, linting and every configured step, reporting the highest exit code. Set
`validate.failFast = true` to stop at the first failure. Validation steps inherit the caller's `PATH`; include their
dependencies in the consumer's dev shell.

## Exported checks

The formatting and linting checks copy `src` into a sandbox and run the complete local file-tool configuration.
`check.prepare` can materialize store-backed state such as dependency trees after each copy:

```nix
project = nix-tools.lib.configure {
  inherit system src;
  check.prepare = "cp -r ${frontendDeps}/node_modules frontend/node_modules";
  check.runtimeInputs = [ pkgs.coreutils ];
};
```

Structural profile checks run directly against immutable source inputs and do not inherit `check.prepare` or
`check.runtimeInputs`; their check derivations therefore behave like their explicit check apps.

There is no `localOnly` splice: if required state cannot be prepared hermetically, use the local apps or validate gate
and do not export `project.checks`.

## Pinning and cache

Keep `nixpkgs-pinned` independent of the consumer's nixpkgs: tool versions and shipped configs are tested together. Use
`toolPkgs` for a deliberate per-project override.

Formatter commands and declared configuration inputs must participate in treefmt's cache key. Built-in adapters handle
this; custom formatters declare additional inputs through `cacheInputs`. Hiding a mutable config inside a wrapper can
otherwise leave stale formatting results.

[`actionlint`]: https://rhysd.github.io/actionlint/
[`biome`]: https://biomejs.dev/
[`buf`]: https://buf.build
[`caddy`]: https://caddyserver.com
[`deadnix`]: https://github.com/astro/deadnix
[`debputy`]: https://salsa.debian.org/debian/debputy
[`djlint`]: https://djlint.com/
[`editorconfig-checker`]: https://github.com/editorconfig-checker/editorconfig-checker
[`gritql`]: https://github.com/biomejs/gritql
[`just`]: https://github.com/casey/just
[`lychee`]: https://github.com/lycheeverse/lychee
[`mago`]: https://mago.carthage.software/
[`mdformat`]: https://mdformat.rtfd.io/
[`msgcat`]: https://www.gnu.org/software/gettext/manual/html_node/msgcat-Invocation.html
[`msgfmt`]: https://www.gnu.org/software/gettext/manual/html_node/msgfmt-Invocation.html
[`nixfmt`]: https://github.com/NixOS/nixfmt
[`oxipng`]: https://github.com/oxipng/oxipng
[`prettier`]: https://prettier.io/
[`ruff`]: https://github.com/astral-sh/ruff
[`rustfmt`]: https://github.com/rust-lang/rustfmt
[`salt-lint`]: https://github.com/warpnet/salt-lint
[`shellcheck`]: https://www.shellcheck.net/
[`shfmt`]: https://github.com/mvdan/sh
[`sqlfluff`]: https://www.sqlfluff.com/
[`statix`]: https://github.com/molybdenumsoftware/statix
[`svgo`]: https://github.com/svg/svgo
[`taplo`]: https://taplo.tamasfe.dev
[`xmllint`]: https://gnome.pages.gitlab.gnome.org/libxml2/xmllint.html
[`yamlfmt`]: https://github.com/google/yamlfmt
[`yamllint`]: https://github.com/adrienverge/yamllint
