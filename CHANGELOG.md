# Changelog

Notable changes, newest first, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Pre-1.0, so a minor
release may carry a breaking change.

A `flake.lock` bump that moves a formatter's output is a minor release, not a chore — every consuming tree reformats, so
for this library `nix flake update` is a semantic change.

## [Unreleased]

### Added

- Opt-in `format.grit` support for native `*.grit` patterns, using the pinned GritQL executable and failure-safe
  replacement.
- Markdown-only Grit pattern collections with colocated explanations, executable patterns, and regression samples.
- Read-only Grit pattern-test checks and apps for every profile, including ungated codemod collections.
- A read-only `grit-check` app that tests every Grit profile, scans every gated profile, attributes each output line,
  collapses passing operations, replays nonblank failure output once, retains terminal colors while respecting
  `NO_COLOR`, and reports one aggregate interactive result without realizing the individual failing check derivations.

### Fixed

- Included gated Grit and ast-grep scans, plus Grit pattern tests, in the normal `lint` and `validate` apps without
  invoking any apply runner or duplicating exported flake checks.
- Scoped Grit traversal to concrete target directories and applied composed exclusions through one rooted explicit
  ignore file, without consulting ambient ignore state or enumerating the repository root.
- Deduplicated files selected through overlapping Grit target roots and classified target-selection errors as runner
  failures.
- Kept the Grit formatter's fixed safe operation out of generic argument overrides.
- Replaced generated store-path headings in composed lint and validation output with stable labels.

## [0.4.0] - 2026-09-07

### Added

- Opt-in, named `lint.ast-grep` profiles with read-only checks, explicit codemod apps, root-relative configuration,
  composed exclusions, independently gated profiles, and package overrides.
- Opt-in, named `lint.grit` profiles backed by nix-gritql, with read-only structural checks, explicit codemod apps,
  composed exclusions, independently gated profiles, and package overrides.

### Changed

- Removed the repository-only golden fixture generator from the root flake's public outputs.

## [0.3.0] - 2026-09-03

### Added

- Opt-in XML and GPX formatting and well-formedness checking through `xmllint`, with deterministic indentation and
  failure-safe in-place writes.

## [0.2.0] - 2026-08-24

### Added

- `lint.extraProjectCheckers` for one-shot checks without trigger files.
- Shared `check.prepare` and `check.runtimeInputs` for hermetic check state.
- Opt-in protobuf, SQL and PO checkers, actionlint `configFile`, and Biome import organization.
- Direct pinned-tool access through `lib.toolPkgsFor system`.
- Required custom-formatter `cacheInputs`, made cache-visible with the real command and argv while retaining Nix
  dependency context.

### Fixed

- Built-in checker batch defaults are inherited unless explicitly overridden.
- `repochk` now discovers the shared project root when invoked from a subdirectory.

### Changed

- Unsupported systems, including `x86_64-darwin`, are rejected at the public boundary.

## [0.1.1] - 2026-08-23

### Fixed

- **Every tool that accepts a config path now takes one.** rustfmt, svgo and buf were missing theirs, so
  `format.svg.configFile` threw on a tool that has had `--config` all along.
- **A config is staged into the store under its own basename.** ruff picks its parser from that name, so a
  `pyproject.toml` arrived as `<hash>-pyproject.toml`, was read as a flat `ruff.toml`, and died on `[project]`.
- **`configure` returns `toolPkgs`.** A spliced checker had no supported way to reach the tool this library pins, which
  is the drift such a checker usually exists to catch.
- **A spliced formatter or checker is destructured strictly, like every toggle.** treefmt accepts an unknown key without
  complaint, so a misspelled `exclude` was dropped and the tool ran over files it was meant to leave alone.
- **A wrapped formatter reads its tool and argv at run time, terminated by `--`.** Passed beside the file list, `po`,
  `svg` and `caddyfile` read `--config` as a file to format. Baked into the wrapper, both were invisible to treefmt's
  cache key — two wrappers differing only in an embedded store path are the same size with the same mtime, so neither a
  config edit nor an svgo bump reformatted anything.

## [0.1.0] - 2026-08-23

- Initial release. `repofmt` over 21 languages, `repochk` over 8 linters, behind one `lib.configure`. See the README.
