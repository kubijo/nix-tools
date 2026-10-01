{ lib }:
file:
let
  inherit (builtins) getContext;
  # Interpolation snapshots Nix paths and preserves derivation/string context.
  policy = "${file}";
in
assert lib.assertMsg (getContext policy != { })
  "whitespace.configFile must be a Nix path (for example ./.editorconfig) or a derivation output, not a mutable runtime path string; the policy must participate in the formatter cache key";
[ policy ]
