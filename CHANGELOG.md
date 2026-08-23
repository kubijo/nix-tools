# Changelog

Notable changes, newest first, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Pre-1.0, so a minor
release may carry a breaking change.

A `flake.lock` bump that moves a formatter's output is a minor release, not a chore — every consuming tree reformats, so
for this library `nix flake update` is a semantic change.

## [Unreleased]

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
