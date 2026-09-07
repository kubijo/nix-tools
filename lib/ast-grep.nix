{
  lib,
  toolPkgsFor,
}:
{
  system,
  src,
  toolPkgs ? toolPkgsFor system,
  treeRootFile ? "flake.nix",
  package ? null,
  profiles,
  exclude ? [ ],
}:
let
  inherit (import ./options.nix { inherit lib; }) astGrepProfileOptions;

  isRelative =
    value:
    lib.isString value
    && value != ""
    && !(lib.hasPrefix "/" value)
    && lib.all (part: part != "..") (lib.splitString "/" value);
  isExclusion = value: isRelative value && !(lib.hasPrefix "!" value);

  profileNames = if lib.isAttrs profiles then lib.attrNames profiles else [ ];
  validProfileName =
    name:
    builtins.stringLength name <= 64 && builtins.match "^[a-z]([a-z0-9-]*[a-z0-9])?$" name != null;

  normalized = lib.mapAttrs (_: astGrepProfileOptions) profiles;
  astGrepPackage = if package == null then toolPkgs.ast-grep else package;

  configureProfile =
    name: profile:
    let
      configPath = if profile.configFile == null then "" else "${src}/${profile.configFile}";

      mkRunner =
        mode:
        let
          runnerName = "ast-grep-${name}-${mode}";
        in
        toolPkgs.writeShellApplication {
          name = runnerName;
          # writeShellApplication renders runtimeEnv as quoted scalar assignments. The JSON
          # quotes are data, and the empty PATH is deliberate because every command is absolute.
          excludeShellChecks = [
            "SC2089"
            "SC2090"
            "SC2123"
          ];
          runtimeInputs = [
            astGrepPackage
            toolPkgs.jq
          ];
          runtimeEnv = {
            PATH = null;
            AST_GREP_EXE = lib.getExe astGrepPackage;
            AST_GREP_JQ = lib.getExe toolPkgs.jq;
            AST_GREP_RUNNER_NAME = runnerName;
            AST_GREP_MODE = mode;
            AST_GREP_TREE_ROOT_FILE = treeRootFile;
            AST_GREP_CONFIG_FILE = profile.configFile;
            AST_GREP_PATHS_JSON = builtins.toJSON profile.paths;
            AST_GREP_EXCLUDES_JSON = builtins.toJSON (exclude ++ profile.exclude);
          };
          text = builtins.readFile ./ast-grep.sh;
          meta.mainProgram = runnerName;
        };

      checkRunner = mkRunner "check";
      applyRunner = mkRunner "apply";
      checkDerivation = toolPkgs.runCommandLocal "ast-grep-${name}" { } ''
        cd ${src}
        export HOME="$TMPDIR"
        ${lib.getExe checkRunner}
        touch "$out"
      '';
    in
    assert lib.assertMsg (profile.configFile != null && isRelative profile.configFile)
      "lint.ast-grep.profiles.${name}.configFile must be a non-empty relative path without '..' segments";
    assert lib.assertMsg (
      builtins.pathExists configPath && builtins.readFileType configPath == "regular"
    ) "lint.ast-grep.profiles.${name}.configFile does not exist in the consumer source";
    assert lib.assertMsg (profile.paths != [ ] && lib.all isRelative profile.paths)
      "lint.ast-grep.profiles.${name}.paths must contain non-empty relative paths without '..' segments";
    assert lib.assertMsg (lib.all isExclusion (exclude ++ profile.exclude))
      "lint.ast-grep.profiles.${name}: effective exclusions must be relative globs without '..' segments or leading '!'";
    assert lib.assertMsg (lib.isBool profile.gate)
      "lint.ast-grep.profiles.${name}.gate must be a Boolean";
    {
      inherit
        checkDerivation
        checkRunner
        applyRunner
        ;
      inherit (profile) gate;
    };

  configured = lib.mapAttrs configureProfile normalized;
  runnable = runner: {
    type = "app";
    program = lib.getExe runner;
  };
in
assert lib.assertMsg (
  lib.isAttrs profiles && profileNames != [ ]
) "lint.ast-grep.profiles must be a non-empty attribute set";
assert lib.assertMsg (lib.all validProfileName profileNames)
  "lint.ast-grep profile names must match ^[a-z]([a-z0-9-]*[a-z0-9])?$ and contain at most 64 characters";
{
  checks = lib.mapAttrs' (
    name: profile: lib.nameValuePair "ast-grep-${name}" profile.checkDerivation
  ) (lib.filterAttrs (_: profile: profile.gate) configured);

  apps = lib.foldl' (
    result: name:
    result
    // {
      "ast-grep-${name}-check" = runnable configured.${name}.checkRunner;
      "ast-grep-${name}-apply" = runnable configured.${name}.applyRunner;
    }
  ) { } profileNames;
}
