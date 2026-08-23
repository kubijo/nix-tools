{ lib, toolPkgsFor }:
let
  options = import ./options.nix { inherit lib; };
in
{
  # The only way in: each primitive behind it carries part of a guarantee.
  configure = import ./configure.nix { inherit lib toolPkgsFor; };

  # Both exported to be extended rather than restated.
  inherit (options) defaultExcludes;

  conf = {
    biome = ../conf/biome.json;
    taplo = ../conf/taplo.toml;
    yamlfmt = ../conf/yamlfmt.yml;
    yamllint = ../conf/yamllint.yml;
    ruff = ../conf/ruff.toml;
    prettier = ../conf/prettier.json;
  };
}
