{ lib }:
excludes: checker:
assert lib.assertMsg (lib.all (pattern: !lib.hasInfix "/" pattern) checker.includes)
  "lint.${checker.name}.includes: use basename globs (for example broken.py or *.py), not slash-containing paths; custom checkers can narrow discovery with searchPaths. Formatter includes support path globs separately.";
# Keep each native glob intact: wrapping a list in braces changes literal commas
# into alternatives and makes already-braced patterns invalid nested groups.
map (
  pattern:
  [
    "--hidden"
    "--no-require-git"
    "--show-errors"
    "--type"
    "file"
    "--print0"
    "--glob"
  ]
  ++ lib.concatMap (p: [
    "--exclude"
    p
  ]) (excludes ++ checker.excludes)
  ++ [
    "--"
    pattern
  ]
) checker.includes
