# Unlike the formatter and checker, PATH is left alone: the steps are a repo's own cargo
# and pytest, which want the dev shell they were started from.
{ lib, toolPkgsFor }:
{
  system,
  toolPkgs ? toolPkgsFor system,
  name ? "validate",
  formatter ? null,
  checker ? null,
  runtimeInputs ? [ ],
  failFast ? false,
  # Shell commands, or `{ name, run }` to label one in the output.
  steps ? [ ],
}:
let
  labelled = step: if lib.isString step then { run = step; } else step;

  render =
    step:
    let
      inherit (labelled step) run;
      label = (labelled step).name or run;
    in
    ''
      printf '\n=== %s\n' ${lib.escapeShellArg label}
      ${run} || ${if failFast then "exit 1" else "failed=1"}
    '';

  all =
    lib.optional (formatter != null) "${lib.getExe formatter} --ci"
    ++ lib.optional (checker != null) (lib.getExe checker)
    ++ steps;

  # The steps take the formatter's own runtime, so `pnpm` and `prettier` share one node.
  inherited = lib.optional (formatter.passthru.nodejs or null != null) formatter.passthru.nodejs;
in
toolPkgs.writeShellApplication {
  inherit name;
  runtimeInputs = inherited ++ runtimeInputs;
  text = ''
    failed=0
    ${lib.concatMapStringsSep "\n" render all}
    exit "$failed"
  '';
}
