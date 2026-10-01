{ lib, toolPkgsFor }:
let
  inherit (builtins) toJSON;
  options = import ./options.nix { inherit lib; };
  inherit (options)
    toggle
    fileCheckerOptions
    linkCheckerOptions
    checkerSpecOptions
    projectCheckerSpecOptions
    debianCheckerOptions
    defaultExcludeFiles
    defaultExcludeDirs
    ;
in
{
  system,
  toolPkgs ? toolPkgsFor system,

  name ? "repochk",
  treeRootFile ? "flake.nix",
  exclude ? [ ],
  unexclude ? [ ],
  excludeDefaults ? true,
  # Under the tree, not $HOME — the `nix flake check` sandbox leaves $HOME unset.
  cacheDir ? ".tmp",
  extraRuntimeInputs ? [ ],
  # A repo's own checker, spliced into this enumeration so it inherits
  # the excludes and the quiet-on-success reporting instead of restating them.
  extraCheckers ? { },
  # Whole-project checks run exactly once and receive no synthetic file arguments.
  extraProjectCheckers ? { },

  nix ? true,
  shell ? true,
  yaml ? true,
  workflows ? true,

  # Language-specific, so off by default like their formatters.
  python ? false,
  php ? false,
  debian ? false,
  whitespace ? false,
  salt ? false,
  javascript ? false,
  typescript ? false,
  # Cross-references rot silently as files move, but a repo has to opt into the reading.
  links ? false,
  protobuf ? false,
  sql ? false,
  po ? false,
  xml ? false,
}:
let
  args =
    lib.mapAttrs (_: toggle fileCheckerOptions) {
      inherit
        nix
        shell
        yaml
        workflows
        python
        php
        whitespace
        salt
        javascript
        typescript
        protobuf
        sql
        po
        xml
        ;
    }
    // {
      # `ignoreLinks` matches a link target rather than a file, so this takes its own schema.
      links = toggle linkCheckerOptions links;
    };

  spliced = lib.mapAttrs (name: spec: { inherit name; } // checkerSpecOptions spec) extraCheckers;

  projectCheckers = lib.attrValues (
    lib.mapAttrs (name: spec: { inherit name; } // projectCheckerSpecOptions spec) projectSpecs
  );

  debianOptions = toggle debianCheckerOptions debian;
  configFlagged = import ./config-path.nix { inherit toolPkgs; };
  projectSpecs =
    lib.optionalAttrs debianOptions.enable {
      debian = {
        command =
          if debianOptions.exe != null then
            debianOptions.exe
          else
            lib.getExe' (
              if debianOptions.package != null then
                debianOptions.package
              else
                toolPkgs.callPackage ../nix/debputy.nix { }
            ) "debputy-nix-tools";
        options = [
          "lint"
          "--linter-exit-code"
        ]
        ++ configFlagged "--config" (
          if debianOptions.configFile != null then debianOptions.configFile else ../conf/debputy.yaml
        )
        ++ debianOptions.extraOptions;
        inherit (debianOptions) exclude;
      };
    }
    // extraProjectCheckers;

  fileCheckers = lib.attrValues (import ./checkers.nix { inherit lib toolPkgs args; } // spliced);
  nameCollisions = lib.intersectLists (map (checker: checker.name) fileCheckers) (
    lib.attrNames projectSpecs
  );

  # fd matches `--exclude` against any path component, so no `**/` form is needed.
  excludes =
    lib.optionals excludeDefaults (
      lib.subtractLists unexclude (defaultExcludeFiles ++ defaultExcludeDirs)
    )
    ++ exclude;

  fdArguments = import ./file-selection.nix { inherit lib; };
  fdArgs = checker: fdArguments excludes checker;

  # Finish discovery before invoking a checker. Process substitution hides fd's
  # status and lets a stdin-reading checker steal subsequent filenames.
  scan =
    checker: root:
    let
      run = "${checker.command} ${lib.escapeShellArgs checker.options}";
      input = if checker.stdin == "null" then "</dev/null" else "";
      discoveryCommands = map (argv: "${lib.getExe toolPkgs.fd} ${lib.escapeShellArgs argv} ${root}") (
        fdArgs checker
      );
      fd = if discoveryCommands == [ ] then ":" else lib.concatStringsSep " && " discoveryCommands;
      fail = subject: ''
        printf 'FAIL (%s): %s\n' ${lib.escapeShellArg checker.name} ${subject} >&2
        if [ -n "$output" ]; then printf '%s\n' "$output" >&2; fi
        failed=$((failed + 1))
      '';
    in
    assert lib.assertMsg (lib.elem checker.stdin [
      "null"
      "inherit"
    ]) "${checker.name}: stdin must be null or inherit";
    ''
      # fd can report traversal errors on stderr while returning success.
      if ! ( ${fd} ) > "$discovery/files" 2> "$discovery/error" || [ -s "$discovery/error" ]; then
        output=$(cat "$discovery/error")
        ${fail "discovery"}
      else
        sort -zu "$discovery/files" > "$discovery/unique"
        mapfile -t -d ''' found < "$discovery/unique"
        ${
          if checker.batch then
            ''
              if [ ''${#found[@]} -gt 0 ]; then
                checked=$((checked + ''${#found[@]}))
                if ! output=$(${run} "''${found[@]}" ${input} 2>&1); then
                  ${fail ''"''${#found[@]} files"''}
                fi
              fi
            ''
          else
            ''
              for file in "''${found[@]}"; do
                checked=$((checked + 1))
                if ! output=$(${run} "$file" ${input} 2>&1); then
                  ${fail ''"$file"''}
                fi
              done
            ''
        }
      fi
    '';

  scanRoot = checker: root: ''
    if output=$(${toolPkgs.python3}/bin/python ${./search-root.py} ${root} 2>&1); then
      ${scan checker root}
    else
      status=$?
      # Only ENOENT is an optional missing root. Permission and other errors fail.
      if [ "$status" -ne 3 ]; then
        printf 'FAIL (%s): discovery\n%s\n' ${lib.escapeShellArg checker.name} "$output" >&2
        failed=$((failed + 1))
      fi
    fi
  '';

  # shellcheck rejects a loop that can only ever run once (SC2043).
  loop =
    checker:
    if checker.searchPaths == [ ] || checker.includes == [ ] then
      ""
    else if lib.length checker.searchPaths == 1 then
      let
        root = lib.escapeShellArg (lib.head checker.searchPaths);
      in
      scanRoot checker root
    else
      ''
        for root in ${lib.escapeShellArgs checker.searchPaths}; do
          ${scanRoot checker "\"$root\""}
        done
      '';

  projectRun =
    checker:
    let
      run = "${checker.command} ${lib.escapeShellArgs checker.options}";
      checkerExcludes = toJSON (excludes ++ checker.excludes);
    in
    ''
      checked_projects=$((checked_projects + 1))
      if ! output=$(REPOCHK_EXCLUDES_JSON=${lib.escapeShellArg checkerExcludes} ${run} 2>&1); then
        printf 'FAIL (%s): project\n' ${lib.escapeShellArg checker.name} >&2
        if [ -n "$output" ]; then printf '%s\n' "$output" >&2; fi
        failed=$((failed + 1))
      fi
    '';
in
assert lib.assertMsg (
  nameCollisions == [ ]
) "a checker name cannot be both file-scoped and project-scoped: ${toString nameCollisions}";
toolPkgs.writeShellApplication {
  inherit name;
  passthru.selection = {
    inherit excludes fileCheckers projectCheckers;
    files = map (checker: checker // { discoveryArgs = fdArgs checker; }) fileCheckers;
  };
  runtimeInputs = [
    toolPkgs.fd
    # PATH is nulled, so even `mkdir` has to be declared.
    toolPkgs.coreutils
    # actionlint silently skips `run:` blocks when it cannot find shellcheck.
    toolPkgs.shellcheck
  ]
  ++ extraRuntimeInputs;
  runtimeEnv.PATH = null;
  excludeShellChecks = [ "SC2123" ];
  text = ''
    tree_root_file=${lib.escapeShellArg treeRootFile}
    root=$PWD
    while [ ! -e "$root/$tree_root_file" ]; do
      if [ "$root" = / ]; then
        echo "${name}: could not find project root marker '$tree_root_file' above '$PWD'" >&2
        exit 1
      fi
      root=''${root%/*}
      [ -n "$root" ] || root=/
    done
    cd "$root"

    export XDG_CACHE_HOME="$PWD/${cacheDir}/cache"
    mkdir -p "$XDG_CACHE_HOME"

    discovery=$(mktemp -d)
    trap 'rm -rf "$discovery"' EXIT

    failed=0
    checked=0
    checked_projects=0

    ${lib.concatMapStringsSep "\n" loop fileCheckers}
    ${lib.concatMapStringsSep "\n" projectRun projectCheckers}

    echo "${name}: checked $checked files and $checked_projects project checks, $failed failed"
    [ "$failed" -eq 0 ]
  '';
}
