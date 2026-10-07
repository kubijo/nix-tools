{ lib, toolPkgs }:
value:
let
  inherit (builtins) toJSON;
  schemas = import ./options.nix { inherit lib; };
  cfg = schemas.toggle schemas.deptryOptions value;
in
assert lib.assertMsg (
  !cfg.enable || (lib.isAttrs cfg.projects && cfg.projects != { })
) "lint.deptry.projects must be a non-empty attribute set";
lib.optionalAttrs cfg.enable (
  lib.mapAttrs' (
    name: raw:
    let
      project = schemas.deptryProjectOptions raw;
      exe =
        if project.exe != null then
          project.exe
        else
          lib.getExe' (if project.package != null then project.package else toolPkgs.deptry) "deptry";
      settings = toolPkgs.writeText "deptry-project.json" (toJSON {
        inherit (project) root sourceRoots extraOptions;
        # Preserve Nix path context and runtime-relative strings.
        configFile = "${project.configFile}";
        inherit exe;
      });
    in
    lib.nameValuePair "deptry-${name}" {
      command = toolPkgs.writeShellScript "deptry-project" ''
        exec ${toolPkgs.python3}/bin/python -I ${./deptry.py} ${settings}
      '';
      inherit (project) outdated;
    }
  ) cfg.projects
)
