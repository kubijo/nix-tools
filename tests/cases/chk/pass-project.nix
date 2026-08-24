{ toolPkgs }:
{
  exclude = [ "global/**" ];
  extraProjectCheckers.once = {
    exclude = [ "local/**" ];
    command = toolPkgs.writeShellScript "project-once" ''
      [ "$#" -eq 0 ]
      [ -f README.txt ]
      case "$REPOCHK_EXCLUDES_JSON" in
        *'"global/**"'*'"local/**"'*) ;;
        *) exit 1 ;;
      esac
    '';
  };
}
