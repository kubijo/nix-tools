{
  lib,
  toolPkgs,
  python,
  system,
}:
let
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      (lib.fileset.fileFilter (file: file.hasExt "py" || file.hasExt "pyi") ../lib)
      (lib.fileset.difference (lib.fileset.fileFilter (
        file: file.hasExt "py" || file.hasExt "pyi"
      ) ./.) ./fixtures)
    ];
  };
  checker =
    (import ../lib/checker.nix {
      inherit lib;
      toolPkgsFor = _: toolPkgs;
    })
      {
        inherit system toolPkgs;
        treeRootFile = "pyproject.toml";
        nix = false;
        shell = false;
        yaml = false;
        workflows = false;
        basedpyright.projects.nix-tools = {
          configFile = "pyproject.toml";
          reporter = "rich";
          inherit python;
        };
      };
in
{
  basedpyright-report = toolPkgs.runCommandLocal "basedpyright-report" { } ''
    ${python}/bin/python ${./basedpyright_report_test.py} ${source}/lib/basedpyright_report.py
    touch "$out"
  '';

  python-typing = toolPkgs.runCommandLocal "python-typing" { } ''
    cp -r ${source} work
    chmod -R u+w work
    cd work
    ${lib.getExe checker}
    touch "$out"
  '';
}
