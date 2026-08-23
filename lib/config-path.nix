# ruff picks its parser from the config's basename, and a store path is `<hash>-name`.
{ toolPkgs }:
flag: config:
let
  name = baseNameOf config;
  staged = toolPkgs.runCommandLocal "config-${name}" { } ''
    install -Dm444 ${config} "$out/${name}"
  '';
in
[
  flag
  "${staged}/${name}"
]
