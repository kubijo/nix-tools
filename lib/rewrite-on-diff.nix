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
  text = ''
    for src in "$@"; do
      tmp=$(mktemp)
      ${render "\"$src\"" "\"$tmp\""}
      # `cat >` rather than `mv`, to keep the original's permissions.
      if [ -s "$tmp" ] && ! cmp -s "$tmp" "$src"; then cat "$tmp" > "$src"; fi
      rm -f "$tmp"
    done
  '';
}
