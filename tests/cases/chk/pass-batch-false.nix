{ toolPkgs }:
{
  javascript = {
    batch = false;
    exe = toolPkgs.writeShellScript "expect-one-file" ''
      files=0
      for arg in "$@"; do
        case "$arg" in
          *.js) files=$((files + 1)) ;;
        esac
      done
      [ "$files" -eq 1 ]
    '';
  };
}
