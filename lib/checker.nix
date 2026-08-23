{ lib, toolPkgsFor }:
let
  options = import ./options.nix { inherit lib; };
  inherit (options)
    toggle
    fileCheckerOptions
    linkCheckerOptions
    defaultExcludeFiles
    defaultExcludeDirs
    ;
in
{
  system,
  toolPkgs ? toolPkgsFor system,

  name ? "repochk",
  exclude ? [ ],
  unexclude ? [ ],
  excludeDefaults ? true,
  # Under the tree, not $HOME — the `nix flake check` sandbox leaves $HOME unset.
  cacheDir ? ".tmp",
  extraRuntimeInputs ? [ ],
  # A repo's own checker, spliced into this enumeration so it inherits
  # the excludes and the quiet-on-success reporting instead of restating them.
  extraCheckers ? { },

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
        ;
    }
    // {
      # `ignoreLinks` matches a link target rather than a file, so this takes its own schema.
      links = toggle linkCheckerOptions links;
    };

  spliced = lib.mapAttrs (
    name: spec:
    {
      inherit name;
      options = [ ];
      excludes = [ ];
      searchPaths = [ "." ];
      batch = false;
    }
    // spec
  ) extraCheckers;

  checkers = lib.attrValues (import ./checkers.nix { inherit lib toolPkgs args; } // spliced);

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
in
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
    export XDG_CACHE_HOME="$PWD/${cacheDir}/cache"
    mkdir -p "$XDG_CACHE_HOME"

    failed=0
    checked=0

    ${lib.concatMapStringsSep "\n" loop checkers}

    echo "${name}: checked $checked files, $failed failed"
    [ "$failed" -eq 0 ]
  '';
}
