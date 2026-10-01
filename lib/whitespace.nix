{ toolPkgs }:
let
  python = toolPkgs.python3.withPackages (p: [ p.editorconfig ]);
in
toolPkgs.writeShellApplication {
  name = "editorconfig-policy";
  text = ''
    exec ${python}/bin/python ${./whitespace.py} "$@"
  '';
}
