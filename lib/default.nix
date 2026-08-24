{
  lib,
  toolPkgsFor,
  supportedSystems ? null,
}:
let
  options = import ./options.nix { inherit lib; };
in
{
  # The only way in: each primitive behind it carries part of a guarantee.
  configure = import ./configure.nix { inherit lib toolPkgsFor supportedSystems; };

  # Available before `configure`, so custom splices need no dummy project or foreign nixpkgs.
  inherit toolPkgsFor;
  supportedSystems = if supportedSystems == null then [ ] else supportedSystems;

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
