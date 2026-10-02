# The whole consumer surface: every guarantee is enforced here, with no second way in.
{
  lib,
  nix-gritql,
  toolPkgsFor,
  pythonEnvsFor,
  supportedSystems ? null,
}:
let
  inherit (import ./options.nix { inherit lib; })
    astGrepOptions
    astGrepProfileOptions
    checkOptions
    defaultExcludeDirs
    defaultExcludeFiles
    gritOptions
    gritProfileOptions
    toggle
    ;
  mkAstGrep = import ./ast-grep.nix { inherit lib toolPkgsFor; };
  mkGrit = import ./grit.nix { inherit lib nix-gritql toolPkgsFor; };
  mkFormatter = import ./formatter.nix {
    inherit lib nix-gritql toolPkgsFor;
  };
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
  coverage ? { },
  outdated ? false,
}:
let
  checkArgs = checkOptions check;
  grit = toggle gritOptions (lint.grit or false);
  astGrep = toggle astGrepOptions (lint.ast-grep or false);
  astGrepProfiles = lib.mapAttrs (_: astGrepProfileOptions) astGrep.profiles;
  structuralLintEnabled = structuralLintSteps != [ ];
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
  fileChecker = mkChecker (
    common
    // withExcludes lintWithoutStructural
    // {
      name = if structuralLintEnabled then "repochk-files" else "repochk";
    }
  );
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
    profile // { exclude = structuralExcludes ++ profile.exclude; }
  ) grit.profiles;
  gritProject =
    if grit.enable then
      mkGrit {
        inherit
          src
          system
          toolPkgs
          treeRootFile
          ;
        inherit (grit) package;
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
        inherit (astGrep) package;
        profiles = astGrepProfiles;
        exclude = structuralExcludes;
      }
    else
      null;
  gritLintSteps = lib.optional grit.enable {
    name = "Grit";
    run = gritProject.apps.grit-check.program;
  };
  astGrepLintSteps = lib.optionals astGrep.enable (
    map (name: {
      name = "ast-grep check: ${name}";
      run = astGrepProject.apps."ast-grep-${name}-check".program;
    }) (lib.attrNames (lib.filterAttrs (_: profile: profile.gate) astGrepProfiles))
  );
  structuralLintSteps = gritLintSteps ++ astGrepLintSteps;
  checker =
    if !structuralLintEnabled then
      fileChecker
    else
      mkValidate {
        inherit system toolPkgs;
        name = "repochk";
        steps = [
          {
            name = "File checks";
            run = lib.getExe fileChecker;
          }
        ]
        ++ structuralLintSteps;
      };
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

  coverageReport = import ./coverage.nix { inherit lib toolPkgs; } {
    inherit formatter src treeRootFile;
    checker = fileChecker;
    config = coverage;
    customCheckers = lib.attrNames (lint.extraCheckers or { });
    customFormatters = lib.attrNames (format.extraFormatters or { });
    debian =
      (toggle (import ./options.nix { inherit lib; }).debianCheckerOptions (lint.debian or false)).enable
      && !((lint.extraProjectCheckers or { }) ? debian);
    projectNames =
      map (spec: "lint:${spec.name}") fileChecker.selection.projectCheckers
      ++ map (step: "validate:${if lib.isString step then step else step.name or step.run}") (
        validate.steps or [ ]
      )
      ++ map (step: "lint:${step.name}") structuralLintSteps;
  };

  outdatedReport =
    import ./outdated.nix
      {
        inherit lib toolPkgs;
        python = (pythonEnvsFor toolPkgs).runtime;
      }
      {
        config = outdated;
        inherit treeRootFile;
        entries = formatter.outdatedEntries ++ fileChecker.outdatedEntries;
      };

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
    coverage = runnable coverageReport;
  }
  // lib.optionalAttrs outdatedReport.enable { outdated = runnable outdatedReport.package; }
  // lib.optionalAttrs grit.enable gritProject.apps
  // lib.optionalAttrs astGrep.enable astGrepProject.apps;

  checks =
    mkChecks {
      inherit
        system
        toolPkgs
        src
        formatter
        ;
      checker = fileChecker;
      inherit (checkArgs) prepare runtimeInputs;
    }
    // lib.optionalAttrs grit.enable gritProject.checks
    // lib.optionalAttrs astGrep.enable astGrepProject.checks;

  # Runtime included, so a shell built from this cannot carry a second node.
  packages = [
    formatter
    checker
    gate
    coverageReport
  ]
  ++ lib.optional (nodejs != null) nodejs
  ++ lib.optional outdatedReport.enable outdatedReport.package;
}
