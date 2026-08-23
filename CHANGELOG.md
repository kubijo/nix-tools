# Changelog

Notable changes, newest first, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Pre-1.0, so a minor
release may carry a breaking change.

A `flake.lock` bump that moves a formatter's output is a minor release, not a chore — every consuming tree reformats, so
for this library `nix flake update` is a semantic change.

## [Unreleased]

## [0.1.0] - 2026-08-23

- Initial release. `repofmt` over 21 languages, `repochk` over 8 linters, behind one `lib.configure`. See the README.
