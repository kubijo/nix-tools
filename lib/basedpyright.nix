{ lib, toolPkgs }:
value:
let
  inherit (builtins) toJSON;
  schemas = import ./options.nix { inherit lib; };
  cfg = schemas.toggle schemas.basedpyrightOptions value;
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./basedpyright.py
      ./basedpyright_report.py
      ./terminal_env.py
    ];
  };
in
assert lib.assertMsg (
  !cfg.enable || (lib.isAttrs cfg.projects && cfg.projects != { })
) "lint.basedpyright.projects must be a non-empty attribute set";
lib.optionalAttrs cfg.enable (
  lib.mapAttrs' (
    name: raw:
    let
      project = schemas.basedpyrightProjectOptions raw;
      runnerPython =
        if project.reporter == "rich" then
          toolPkgs.python3.withPackages (ps: [ ps.rich ])
        else
          toolPkgs.python3;
      settings = toolPkgs.writeText "basedpyright-project.json" (toJSON {
        inherit (project) configFile reporter;
        python = "${project.python}/bin/python";
        exe =
          if project.exe != null then
            project.exe
          else
            lib.getExe' (
              if project.package != null then project.package else toolPkgs.basedpyright
            ) "basedpyright";
      });
    in
    lib.nameValuePair "basedpyright-${name}" {
      command = toolPkgs.writeShellScript "basedpyright-project" ''
        exec ${runnerPython}/bin/python -I ${source}/basedpyright.py ${settings}
      '';
      inherit (project) outdated;
    }
  ) cfg.projects
)
