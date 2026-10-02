{ lib, toolPkgs }:
toolPkgs.writeShellApplication {
  name = "nix-tools-npm-report";
  text = ''
    exec ${lib.getExe toolPkgs.nodejs} ${./outdated/npm-report.cjs} ${toolPkgs.nodejs}/lib/node_modules/npm/package.json "$@"
  '';
}
