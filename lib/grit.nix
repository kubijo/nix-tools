{
  lib,
  nix-gritql,
  toolPkgsFor,
}:
{
  system,
  src,
  profiles,
  toolPkgs ? toolPkgsFor system,
  treeRootFile ? "flake.nix",
  package ? null,
}:
let
  inherit (builtins)
    baseNameOf
    length
    match
    pathExists
    readDir
    readFile
    readFileType
    stringLength
    ;
  isRelative =
    value:
    lib.isString value
    && value != ""
    && !(lib.hasPrefix "/" value)
    && !(lib.hasInfix "\n" value)
    && lib.all (part: part != "..") (lib.splitString "/" value);
  isExclusion = value: isRelative value && !(lib.hasPrefix "!" value);
  validProfileName =
    name: stringLength name <= 64 && match "^[a-z]([a-z0-9-]*[a-z0-9])?$" name != null;
  validPatternName = name: match "^[a-z][a-z0-9_]*$" name != null;
  validLogLevel = value: lib.isString value && value != "" && !(lib.hasPrefix "-" value);
  safeGritArgs =
    {
      args,
      switches ? [ ],
    }:
    let
      validate =
        remaining:
        if remaining == [ ] then
          true
        else
          let
            argument = lib.head remaining;
            rest = lib.tail remaining;
          in
          if lib.elem argument switches then
            validate rest
          else if argument == "--log-level" then
            rest != [ ] && validLogLevel (lib.head rest) && validate (lib.tail rest)
          else if lib.hasPrefix "--log-level=" argument then
            validLogLevel (lib.removePrefix "--log-level=" argument) && validate rest
          else
            false;
    in
    lib.isList args
    && lib.all (argument: lib.isString argument && !(lib.hasInfix "\n" argument)) args
    && validate args;

  walk =
    directory: prefix:
    lib.concatLists (
      lib.mapAttrsToList (
        name: type:
        if type == "directory" then
          walk (directory + "/${name}") "${prefix}${name}/"
        else
          [
            {
              path = directory + "/${name}";
              relative = "${prefix}${name}";
              valid = type == "regular" && lib.hasSuffix ".md" name;
            }
          ]
      ) (readDir directory)
    );

  writeLines =
    name: values: toolPkgs.writeText name (lib.concatMapStrings (value: "${value}\n") values);
  gritPackage = if package == null then nix-gritql.lib.mkGrit { inherit toolPkgs; } else package;
  profileNames = if lib.isAttrs profiles then lib.attrNames profiles else [ ];
  configureProfile =
    name: profile:
    let
      patternType =
        if profile.patterns != null && pathExists profile.patterns then
          readFileType profile.patterns
        else
          null;
      entries = if patternType == "directory" then walk profile.patterns "" else [ ];
      patternFiles = lib.filter (entry: entry.valid) entries;
      invalidEntries = lib.filter (entry: !entry.valid) entries;
      patternNames = map (entry: lib.removeSuffix ".md" (baseNameOf entry.relative)) patternFiles;
      gritFenceCounts = map (
        entry: length (lib.splitString "```grit" (readFile entry.path)) - 1
      ) patternFiles;
      uniquePatternNames = lib.unique patternNames;

      generatedConfig = (toolPkgs.formats.yaml { }).generate "grit.yaml" {
        version = "0.0.3";
        patterns = map (entry: { file = "sources/${entry.relative}"; }) patternFiles;
      };
      patternRoot = toolPkgs.linkFarm "grit-${name}-patterns" (
        [
          {
            name = ".grit/grit.yaml";
            path = generatedConfig;
          }
        ]
        ++ map (entry: {
          name = ".grit/sources/${entry.relative}";
          inherit (entry) path;
        }) patternFiles
      );

      pathsFile = writeLines "grit-${name}-paths" profile.paths;
      excludesFile = writeLines "grit-${name}-excludes" profile.exclude;
      commonArgsFile = writeLines "grit-${name}-common-args" profile.gritArgs.common;
      checkArgsFile = writeLines "grit-${name}-check-args" profile.gritArgs.check;
      applyArgsFile = writeLines "grit-${name}-apply-args" profile.gritArgs.apply;

      mkRunner =
        mode:
        let
          runnerName = "grit-${name}-${mode}";
        in
        toolPkgs.writeShellApplication {
          name = runnerName;
          excludeShellChecks = [
            # These are emitted by writeShellApplication's scalar runtimeEnv assignments.
            "SC2123"
            "SC2209"
            # ShellCheck does not recognize invocation through an EXIT trap.
            "SC2329"
          ];
          runtimeEnv = {
            PATH = null;
            GRIT_RUNNER_NAME = runnerName;
            GRIT_RUNNER_MODE = mode;
            GRIT_TREE_ROOT_FILE = treeRootFile;
            GRIT_PATTERN_ROOT = patternRoot;
            GRIT_PATHS_FILE = pathsFile;
            GRIT_EXCLUDES_FILE = excludesFile;
            GRIT_COMMON_ARGS_FILE = commonArgsFile;
            GRIT_CHECK_ARGS_FILE = checkArgsFile;
            GRIT_APPLY_ARGS_FILE = applyArgsFile;
            GRIT_EXE = lib.getExe gritPackage;
            GRIT_FD = lib.getExe toolPkgs.fd;
            GRIT_MKDIR = lib.getExe' toolPkgs.coreutils "mkdir";
            GRIT_MKTEMP = lib.getExe' toolPkgs.coreutils "mktemp";
            GRIT_RM = lib.getExe' toolPkgs.coreutils "rm";
            GRIT_SORT = lib.getExe' toolPkgs.coreutils "sort";
            GRIT_TELEMETRY_DISABLED = "true";
          };
          text = readFile ./grit-runner.sh;
          meta.mainProgram = runnerName;
        };

      checkRunner = mkRunner "check";
      applyRunner = mkRunner "apply";
      testRunner = mkRunner "test";
      checkDerivation = toolPkgs.runCommandLocal "grit-${name}" { } ''
        cd ${src}
        ${lib.getExe checkRunner}
        touch "$out"
      '';
      testDerivation = toolPkgs.runCommandLocal "grit-${name}-test" { } ''
        ${lib.getExe testRunner}
        touch "$out"
      '';
    in
    assert lib.assertMsg (
      patternType == "directory"
    ) "lint.grit.profiles.${name}.patterns must be an existing directory";
    assert lib.assertMsg (
      patternFiles != [ ]
    ) "lint.grit.profiles.${name}.patterns must contain at least one Markdown pattern";
    assert lib.assertMsg (
      invalidEntries == [ ]
    ) "lint.grit.profiles.${name}.patterns may contain only directories and regular .md files";
    assert lib.assertMsg (lib.all validPatternName patternNames)
      "lint.grit.profiles.${name}: pattern filenames must match ^[a-z][a-z0-9_]*\\.md$";
    assert lib.assertMsg (lib.all (count: count == 1)
      gritFenceCounts
    ) "lint.grit.profiles.${name}: every Markdown pattern must contain exactly one ```grit fence";
    assert lib.assertMsg (
      length patternNames == length uniquePatternNames
    ) "lint.grit.profiles.${name}: Markdown pattern filenames must be unique across the collection";
    assert lib.assertMsg
      (
        profile.paths != [ ]
        && lib.all (
          path: isRelative path && pathExists "${src}/${path}" && readFileType "${src}/${path}" == "directory"
        ) profile.paths
      )
      "lint.grit.profiles.${name}.paths must contain existing relative directories without '..' segments";
    assert lib.assertMsg (lib.all isExclusion profile.exclude)
      "lint.grit.profiles.${name}.exclude must contain relative globs without '..' segments or leading '!'";
    assert lib.assertMsg (lib.isBool profile.gate) "lint.grit.profiles.${name}.gate must be a Boolean";
    assert lib.assertMsg (safeGritArgs {
      args = profile.gritArgs.common;
    }) "lint.grit.profiles.${name}.gritArgs.common accepts only --log-level VALUE or --log-level=VALUE";
    assert lib.assertMsg (safeGritArgs {
      args = profile.gritArgs.check;
      switches = [ "--verbose" ];
    }) "lint.grit.profiles.${name}.gritArgs.check accepts only --verbose and --log-level";
    assert lib.assertMsg (safeGritArgs {
      args = profile.gritArgs.apply;
      switches = [ "--verbose" ];
    }) "lint.grit.profiles.${name}.gritArgs.apply accepts only --verbose and --log-level";
    {
      inherit
        applyRunner
        checkDerivation
        checkRunner
        testDerivation
        testRunner
        ;
      inherit (profile) gate;
    };

  configured = lib.mapAttrs configureProfile profiles;
  aggregateOperations = lib.concatMap (
    name:
    [
      {
        inherit name;
        operation = "test";
        runner = configured.${name}.testRunner;
      }
    ]
    ++ lib.optional configured.${name}.gate {
      inherit name;
      operation = "check";
      runner = configured.${name}.checkRunner;
    }
  ) profileNames;
  aggregateManifest = toolPkgs.writeText "grit-check-manifest" (
    lib.concatMapStrings (
      entry: "${entry.name}\t${entry.operation}\t${lib.getExe entry.runner}\n"
    ) aggregateOperations
  );
  aggregateRunner = toolPkgs.writeShellApplication {
    name = "grit-check";
    runtimeEnv = {
      GRIT_MANIFEST = aggregateManifest;
      GRIT_MKTEMP = lib.getExe' toolPkgs.coreutils "mktemp";
      GRIT_RM = lib.getExe' toolPkgs.coreutils "rm";
      GRIT_SED = lib.getExe toolPkgs.gnused;
    };
    text = readFile ./grit-aggregate.sh;
    meta.mainProgram = "grit-check";
  };
  runnable = runner: {
    type = "app";
    program = lib.getExe runner;
  };
in
assert lib.assertMsg (
  lib.isAttrs profiles && profileNames != [ ]
) "lint.grit.profiles must be a non-empty attribute set";
assert lib.assertMsg (lib.all validProfileName profileNames)
  "lint.grit profile names must match ^[a-z]([a-z0-9-]*[a-z0-9])?$ and contain at most 64 characters";
{
  checks = lib.foldl' (
    result: name:
    result
    // {
      "grit-${name}-test" = configured.${name}.testDerivation;
    }
    // lib.optionalAttrs configured.${name}.gate {
      "grit-${name}" = configured.${name}.checkDerivation;
    }
  ) { } profileNames;

  apps = {
    grit-check = runnable aggregateRunner;
  }
  // lib.foldl' (
    result: name:
    result
    // {
      "grit-${name}-check" = runnable configured.${name}.checkRunner;
      "grit-${name}-apply" = runnable configured.${name}.applyRunner;
      "grit-${name}-test" = runnable configured.${name}.testRunner;
    }
  ) { } profileNames;
}
