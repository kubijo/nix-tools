# Only a real diff reaches the file, so an idempotent run leaves mtime untouched.
{ toolPkgsFor }:
{
  system,
  toolPkgs ? toolPkgsFor system,
  name,
  runtimeInputs ? [ ],
  # Receives the quoted source and destination, and must write the destination.
  render,
}:
toolPkgs.writeShellApplication {
  inherit name;
  runtimeInputs = runtimeInputs ++ [
    toolPkgs.coreutils
    toolPkgs.diffutils
  ];
  # `$tool` and `args` arrive on the command line, ended by `--`, so treefmt's cache key sees
  # them. Baked in, they would be invisible: it hashes options, never the command's contents.
  text = ''
    opts=()
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--" ]; then
        shift
        break
      fi
      opts+=("$1")
      shift
    done
    tool="''${opts[0]}"
    args=("''${opts[@]:1}")

    for src in "$@"; do
      tmp=$(mktemp)
      ${render "\"$src\"" "\"$tmp\""}
      # `cat >` rather than `mv`, to keep the original's permissions.
      if [ -s "$tmp" ] && ! cmp -s "$tmp" "$src"; then cat "$tmp" > "$src"; fi
      rm -f "$tmp"
    done
  '';
}
