# The whole consumer surface: every guarantee is enforced here, with no second way in.
{
  lib,
  toolPkgsFor,
  supportedSystems ? null,
}:
let
  inherit (import ./options.nix { inherit lib; }) checkOptions;
  mkFormatter = import ./formatter.nix { inherit lib toolPkgsFor; };
  mkChecker = import ./checker.nix { inherit lib toolPkgsFor; };
  mkChecks = import ./checks.nix { inherit lib toolPkgsFor; };
  mkValidate = import ./validate.nix { inherit lib toolPkgsFor; };

  # A tool answering to a different one of these is not a configuration, but a second repo.
  projectWide = [
    "system"
    "toolPkgs"
    "unexclude"
    "excludeDefaults"
    "nodejs"
    "treeRootFile"
  ];

  gateOwned = projectWide ++ [
    "formatter"
    "checker"
  ];
in
{
  system,
  toolPkgs ? toolPkgsFor system,
  # Usually the consumer's `self`.
  src,
  # Required by any enabled tool that runs on it.
  nodejs ? null,
  # Both runners discover this marker upward, so subdirectory invocations share one root.
  treeRootFile ? "flake.nix",
  # Both tools. `format.exclude` and `lint.exclude` narrow to one, a language's to itself.
  exclude ? [ ],
  unexclude ? [ ],
  excludeDefaults ? true,
  # Passed on to the formatter, the checker and the local gate respectively.
  format ? { },
  lint ? { },
  validate ? { },
  # Shared, hermetic preparation for both exported check derivations.
  check ? { },
}:
let
  checkArgs = checkOptions check;
  common = {
    inherit
      system
      toolPkgs
      unexclude
      excludeDefaults
      treeRootFile
      ;
  };

  withExcludes = cfg: cfg // { exclude = exclude ++ (cfg.exclude or [ ]); };

  formatter = mkFormatter (common // { inherit nodejs; } // withExcludes format);
  checker = mkChecker (common // withExcludes lint);
  gate = mkValidate (
    {
      inherit
        system
        toolPkgs
        formatter
        checker
        ;
    }
    // validate
  );

  misplaced = owned: cfg: lib.intersectLists owned (lib.attrNames cfg);

  runnable = drv: {
    type = "app";
    program = lib.getExe drv;
  };
in
assert lib.assertMsg (supportedSystems == null || lib.elem system supportedSystems)
  "unsupported system `${system}`; nix-tools' pinned tool set supports: ${lib.concatStringsSep ", " supportedSystems}";
assert lib.assertMsg (
  misplaced projectWide format == [ ]
) "these belong to the repo, not to `format`: ${toString (misplaced projectWide format)}";
assert lib.assertMsg (
  misplaced projectWide lint == [ ]
) "these belong to the repo, not to `lint`: ${toString (misplaced projectWide lint)}";
assert lib.assertMsg (misplaced gateOwned validate == [ ])
  "the gate runs this repo's own tools, so it cannot take: ${toString (misplaced gateOwned validate)}";
{
  # Only what a flake output needs a derivation for; the rest is runnable, not rebuildable.
  inherit formatter;

  # So a spliced checker can wrap the pinned tool rather than drift from it.
  inherit toolPkgs;

  apps = {
    format = runnable formatter;
    lint = runnable checker;
    validate = runnable gate;
  };

  checks = mkChecks {
    inherit
      system
      toolPkgs
      src
      formatter
      checker
      ;
    inherit (checkArgs) prepare runtimeInputs;
  };

  # Runtime included, so a shell built from this cannot carry a second node.
  packages = [
    formatter
    checker
    gate
  ]
  ++ lib.optional (nodejs != null) nodejs;
}
