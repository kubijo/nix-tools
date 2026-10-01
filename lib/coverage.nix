{ lib, toolPkgs }:
{
  formatter,
  checker,
  src,
  treeRootFile,
  projectNames,
  customCheckers,
  customFormatters,
  config ? { },
  debian ? false,
}:
let
  inherit (builtins) toJSON;
  schema =
    {
      exceptions ? [ ],
      projectChecks ? { },
      checkerKinds ? { },
      required ? [
        "format"
        "lint"
      ],
    }:
    {
      inherit
        exceptions
        projectChecks
        checkerKinds
        required
        ;
    };
  cfg = schema config;
  kinds = [
    "semantic"
    "syntax"
    "whitespace"
    "test"
    "unspecified"
  ];
  stages = [
    "format"
    "lint"
    "tests"
  ];
  declaration =
    {
      includes,
      kind,
      description,
      exclude ? [ ],
    }:
    assert lib.assertMsg (
      lib.elem kind kinds && description != ""
    ) "coverage project checks need a valid kind and nonempty description";
    {
      inherit
        includes
        kind
        description
        exclude
        ;
    };
  exception =
    {
      includes,
      reason,
      stages ? [
        "format"
        "lint"
        "tests"
      ],
      exclude ? [ ],
    }:
    assert lib.assertMsg (
      lib.isString reason && lib.strings.trim reason != ""
    ) "coverage exceptions require a nonempty reason";
    assert lib.assertMsg (lib.all (
      s:
      lib.elem s [
        "format"
        "lint"
        "tests"
      ]
    ) stages) "invalid coverage exception stage";
    {
      inherit
        includes
        reason
        stages
        exclude
        ;
    };
  declarations = lib.mapAttrs (_: declaration) cfg.projectChecks;
  recorder = toolPkgs.writeShellScript "coverage-record" ''
    exec ${toolPkgs.python3}/bin/python ${./coverage.py} --record "$@"
  '';
  treefmtConfig = toolPkgs.treefmt.buildConfig {
    on-unmatched = "debug";
    inherit (formatter.selection) excludes;
    formatter = lib.mapAttrs (
      name: spec:
      spec
      // {
        command = recorder;
        options = [ name ];
      }
    ) formatter.selection.formatters;
  };
  metadata = toolPkgs.writeText "coverage.json" (toJSON {
    inherit
      treeRootFile
      treefmtConfig
      declarations
      projectNames
      ;
    src = "${src}";
    inherit (cfg) required;
    exceptions = map exception cfg.exceptions;
    formatKinds = lib.mapAttrs (
      name: _:
      if name == "whitespace" && !(lib.elem name customFormatters) then "whitespace" else "format"
    ) formatter.selection.formatters;
    files = map (spec: {
      inherit (spec) name searchPaths discoveryArgs;
      kind =
        cfg.checkerKinds.${spec.name} or (
          if lib.elem spec.name customCheckers then
            "unspecified"
          else if spec.name == "whitespace" then
            "whitespace"
          else
            "semantic"
        );
    }) checker.selection.files;
    debian =
      if debian then
        {
          inherit (lib.findFirst (spec: spec.name == "debian") null checker.selection.projectCheckers)
            command
            ;
          excludes =
            checker.selection.excludes
            ++ (lib.findFirst (spec: spec.name == "debian") null checker.selection.projectCheckers).excludes;
        }
      else
        null;
  });
in
assert lib.assertMsg (lib.all (
  stage: lib.elem stage stages
) cfg.required) "coverage.required must contain format, lint, or tests";
assert lib.assertMsg (lib.all (kind: lib.elem kind kinds) (
  lib.attrValues cfg.checkerKinds
)) "invalid coverage.checkerKinds value";
assert lib.assertMsg (lib.all (name: lib.elem name projectNames) (
  lib.attrNames declarations
)) "coverage.projectChecks must reference a configured lint:<name> or validate:<step name> check";
assert lib.assertMsg (lib.all (name: lib.elem name customCheckers) (
  lib.attrNames cfg.checkerKinds
)) "coverage.checkerKinds may classify only custom file checkers";
toolPkgs.writeShellApplication {
  name = "repo-coverage";
  runtimeInputs = [
    toolPkgs.gitMinimal
    toolPkgs.fd
    toolPkgs.treefmt
  ];
  text = ''
    exec ${toolPkgs.python3}/bin/python ${./coverage.py} ${metadata} "$@"
  '';
}
