{ lib, toolPkgs }:
value:
let
  schemas = import ./options.nix { inherit lib; };
  cfg = schemas.toggle schemas.phpstanOptions value;
in
assert lib.assertMsg (
  !cfg.enable || (lib.isAttrs cfg.projects && cfg.projects != { })
) "lint.phpstan.projects must be a non-empty attribute set";
lib.optionalAttrs cfg.enable (
  lib.mapAttrs' (
    name: raw:
    let
      project = schemas.phpstanProjectOptions raw;
      settings = toolPkgs.writeText "phpstan-project.json" (
        builtins.toJSON {
          inherit (project) root configFile extraOptions;
          php = "${project.php}/bin/php";
          exe =
            if project.exe != null then
              project.exe
            else
              lib.getExe' (
                if project.package != null then
                  project.package
                else
                  toolPkgs.phpstan.override { inherit (project) php; }
              ) "phpstan";
        }
      );
    in
    lib.nameValuePair "phpstan-${name}" {
      command = toolPkgs.writeShellScript "phpstan-project" ''
        exec ${toolPkgs.python3}/bin/python -I ${./phpstan.py} ${settings}
      '';
      inherit (project) outdated;
    }
  ) cfg.projects
)
