{ lib, toolPkgsFor }:
{
  system,
  toolPkgs ? toolPkgsFor system,
  # Usually the consumer's `self`.
  src,
  formatter,
  checker,
  prepare ? "",
  runtimeInputs ? [ ],
}:
let
  workCopy = ''
    cp -r ${src} work && chmod -R u+w work && cd work
    export HOME="$TMPDIR"
    ${prepare}
  '';
in
{
  # `--ci` implies `--fail-on-change`: formatted and idempotent in one assertion.
  formatting = toolPkgs.runCommandLocal "check-formatting" { nativeBuildInputs = runtimeInputs; } ''
    ${workCopy}
    ${lib.getExe formatter} --ci
    touch "$out"
  '';

  linting = toolPkgs.runCommandLocal "check-linting" { nativeBuildInputs = runtimeInputs; } ''
    ${workCopy}
    ${lib.getExe checker}
    touch "$out"
  '';
}
