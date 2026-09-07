# The whole consumer surface: every guarantee is enforced here, with no second way in.
{
  lib,
  nix-gritql,
  toolPkgsFor,
  supportedSystems ? null,
}:
let
  inherit (import ./options.nix { inherit lib; })
    astGrepOptions
    checkOptions
    defaultExcludeDirs
    defaultExcludeFiles
    gritOptions
    gritProfileOptions
    toggle
    ;
  mkAstGrep = import ./ast-grep.nix { inherit lib toolPkgsFor; };
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
  # Every runner discovers this marker upward, so subdirectory invocations share one root.
  treeRootFile ? "flake.nix",
  # Every tool. `format.exclude` and `lint.exclude` narrow by phase, a profile's to itself.
  exclude ? [ ],
  unexclude ? [ ],
  excludeDefaults ? true,
  # Passed on to the formatter, the checker and the local gate respectively.
  format ? { },
  lint ? { },
  validate ? { },
  # Shared, hermetic preparation for the formatting and linting check derivations.
  check ? { },
}:
let
  checkArgs = checkOptions check;
  grit = toggle gritOptions (lint.grit or false);
  astGrep = toggle astGrepOptions (lint.ast-grep or false);
  lintWithoutStructural = removeAttrs lint [
    "grit"
    "ast-grep"
  ];
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
  checker = mkChecker (common // withExcludes lintWithoutStructural);
  expandStructuralDefault =
    value:
    if lib.elem value defaultExcludeDirs then
      [
        "${value}/**"
        "**/${value}/**"
      ]
    else
      [ value ];
  structuralExcludes =
    lib.optionals excludeDefaults (
      lib.concatMap expandStructuralDefault (
        lib.subtractLists unexclude (defaultExcludeFiles ++ defaultExcludeDirs)
      )
    )
    ++ exclude
    ++ (lint.exclude or [ ]);
  gritProfiles = lib.mapAttrs (
    name: rawProfile:
    let
      profile = gritProfileOptions rawProfile;
    in
    assert lib.assertMsg (profile.patterns != null) "lint.grit.profiles.${name}.patterns is required";
    assert lib.assertMsg (
      profile.paths != [ ]
    ) "lint.grit.profiles.${name}.paths must contain at least one target";
    profile
    // {
      inherit treeRootFile;
      exclude = structuralExcludes ++ profile.exclude;
    }
    // lib.optionalAttrs (grit.package != null) { gritPackage = grit.package; }
  ) grit.profiles;
  gritProject =
    if grit.enable then
      nix-gritql.lib.configureProfiles {
        inherit
          src
          system
          toolPkgs
          ;
        profiles = gritProfiles;
      }
    else
      null;
  astGrepProject =
    if astGrep.enable then
      mkAstGrep {
        inherit
          system
          src
          toolPkgs
          treeRootFile
          ;
        inherit (astGrep) package profiles;
        exclude = structuralExcludes;
      }
    else
      null;
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
assert lib.assertMsg (
  !grit.enable || (lib.isAttrs grit.profiles && grit.profiles != { })
) "lint.grit.profiles must be a non-empty attribute set";
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
  }
  // lib.optionalAttrs grit.enable gritProject.apps
  // lib.optionalAttrs astGrep.enable astGrepProject.apps;

  checks =
    mkChecks {
      inherit
        system
        toolPkgs
        src
        formatter
        checker
        ;
      inherit (checkArgs) prepare runtimeInputs;
    }
    // lib.optionalAttrs grit.enable gritProject.checks
    // lib.optionalAttrs astGrep.enable astGrepProject.checks;

  # Runtime included, so a shell built from this cannot carry a second node.
  packages = [
    formatter
    checker
    gate
  ]
  ++ lib.optional (nodejs != null) nodejs;
}
