{ toolPkgs }:
{
  extraCheckers.forbidden = {
    command = toolPkgs.writeShellScript "forbidden" ''
      ! ${toolPkgs.gnugrep}/bin/grep -q forbidden "$@"
    '';
    includes = [ "*.txt" ];
    batch = true;
  };
}
