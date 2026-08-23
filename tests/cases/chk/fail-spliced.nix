# The store path is not incidental: the runner nulls PATH, so a bare `grep` exits 127
# and the negation reads that as a pass.
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
