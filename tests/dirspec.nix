{
  api,
  lib,
  toolPkgs,
  python,
}:
let
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../lib/dirspec.py
      ../lib/terminal_env.py
      ./dirspec_test.py
      ../examples/dirspec
    ];
  };
  package = (api.packagesFor toolPkgs).dirspec;
  spec = toolPkgs.writeText "dirspec-smoke.toml" ''
    version = 1
    strict = true
    [paths]
    "bin/app" = { type = "file", mode = 0o755, min_size = 1 }
  '';
in
{
  dirspec = toolPkgs.runCommandLocal "dirspec-tests" { } ''
    ${python}/bin/python ${source}/tests/dirspec_test.py ${source}/lib
    touch "$out"
  '';

  dirspec-package = toolPkgs.runCommandLocal "dirspec-package" { } ''
    ${lib.getExe package} --help > /dev/null
    mkdir -p tree/bin
    printf 'app\n' > tree/bin/app
    chmod 755 tree/bin/app
    ${lib.getExe package} tree --spec ${spec}
    touch tree/extra
    if ${lib.getExe package} tree --spec ${spec}; then
      echo 'dirspec accepted an undeclared file in strict mode' >&2
      exit 1
    else
      test "$?" -eq 1
    fi
    ${lib.getExe package} tree --spec ${spec} --no-strict
    touch "$out"
  '';
}
