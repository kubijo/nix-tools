{ toolPkgs }:
let
  countFiles =
    expected:
    toolPkgs.writeShellScript "expect-${toString expected}-files" ''
      [ "$#" -eq ${toString expected} ]
    '';
in
{
  extraCheckers = {
    default-per-file = {
      command = countFiles 1;
      includes = [ "*.txt" ];
    };
    explicit-batch = {
      command = countFiles 2;
      includes = [ "*.txt" ];
      batch = true;
    };
  };
}
