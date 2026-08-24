{ toolPkgs }:
{
  extraProjectCheckers.broken.command = toolPkgs.writeShellScript "broken-project" ''
    echo "project failure" >&2
    exit 1
  '';
}
