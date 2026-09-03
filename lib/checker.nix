{ lib, toolPkgsFor }:
let
  options = import ./options.nix { inherit lib; };
  inherit (options)
    toggle
    fileCheckerOptions
    linkCheckerOptions
    checkerSpecOptions
    projectCheckerSpecOptions
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
    lib.mapAttrs (name: spec: { inherit name; } // projectCheckerSpecOptions spec) extraProjectCheckers
  );

  fileCheckers = lib.attrValues (import ./checkers.nix { inherit lib toolPkgs args; } // spliced);
  nameCollisions = lib.intersectLists (map (checker: checker.name) fileCheckers) (
    lib.attrNames extraProjectCheckers
  );

  # fd matches `--exclude` against any path component, so no `**/` form is needed.
  excludes =
    lib.optionals excludeDefaults (
      lib.subtractLists unexclude (defaultExcludeFiles ++ defaultExcludeDirs)
    )
    ++ exclude;

  fdArgs =
    checker:
    lib.escapeShellArgs (
      [
        "--hidden"
        # Applies .gitignore with no checkout, so sandbox and worktree enumerate alike.
        "--no-require-git"
        "--type"
        "file"
        "--print0"
        "--glob"
      ]
      ++ lib.concatMap (p: [
        "--exclude"
        p
      ]) (excludes ++ checker.excludes)
      ++ [ "{${lib.concatStringsSep "," checker.includes}}" ]
    );

  # `--print0` throughout, so a path holding a newline survives the round trip.
  # `batch` pays a tool's startup cost once rather than per file.
  scan =
    checker: root:
    let
      run = "${checker.command} ${lib.escapeShellArgs checker.options}";
      fd = "${lib.getExe toolPkgs.fd} ${fdArgs checker} ${root}";
      fail = subject: ''
        printf 'FAIL (%s): %s\n' ${lib.escapeShellArg checker.name} ${subject} >&2
        if [ -n "$output" ]; then printf '%s\n' "$output" >&2; fi
        failed=$((failed + 1))
      '';
    in
    if checker.batch then
      ''
        mapfile -t -d ''' found < <(${fd})
        if [ ''${#found[@]} -gt 0 ]; then
          checked=$((checked + ''${#found[@]}))
          if ! output=$(${run} "''${found[@]}" 2>&1); then
            ${fail ''"''${#found[@]} files"''}
          fi
        fi
      ''
    else
      ''
        while IFS= read -r -d ''' file; do
          checked=$((checked + 1))
          if ! output=$(${run} "$file" 2>&1); then
            ${fail ''"$file"''}
          fi
        done < <(${fd})
      '';

  # shellcheck rejects a loop that can only ever run once (SC2043).
  loop =
    checker:
    if checker.searchPaths == [ "." ] then
      scan checker "."
    else
      ''
        for root in ${lib.escapeShellArgs checker.searchPaths}; do
          [ -d "$root" ] || continue
          ${scan checker "\"$root\""}
        done
      '';

  projectRun =
    checker:
    let
      run = "${checker.command} ${lib.escapeShellArgs checker.options}";
      checkerExcludes = builtins.toJSON (excludes ++ checker.excludes);
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

    failed=0
    checked=0
    checked_projects=0

    ${lib.concatMapStringsSep "\n" loop fileCheckers}
    ${lib.concatMapStringsSep "\n" projectRun projectCheckers}

    echo "${name}: checked $checked files and $checked_projects project checks, $failed failed"
    [ "$failed" -eq 0 ]
  '';
}
