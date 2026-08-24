{ toolPkgs }:
let
  expectTwoFiles = toolPkgs.writeShellScript "expect-two-files" ''
    files=0
    for arg in "$@"; do
      case "$arg" in
        *.js | *.py) files=$((files + 1)) ;;
      esac
    done
    [ "$files" -eq 2 ]
  '';
in
{
  javascript.exe = expectTwoFiles;
  python.exe = expectTwoFiles;
}
