{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  fixtureRoot = ./fixtures/grit;
  patterns = fixtureRoot + "/patterns";
  configure =
    src:
    api.configure {
      inherit src system;
      lint = {
        exclude = [ "source-two/excluded.yaml" ];
        grit.profiles.main = {
          inherit patterns;
          paths = [
            "source one"
            "source-two"
          ];
          gritArgs.common = [
            "--log-level"
            "info"
          ];
        };
      };
    };
  cleanProject = configure (fixtureRoot + "/clean");
  violationProject = configure (fixtureRoot + "/violation");
  profiledProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles = {
      policy = {
        inherit patterns;
        paths = [ "source one" ];
      };
      rename-data = {
        inherit patterns;
        paths = [ "source-two" ];
        exclude = [ "source-two/excluded.yaml" ];
        gate = false;
      };
    };
  };

  disabledProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
  };
  emptyProfilesProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit = true;
  };
  missingPatternsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main.paths = [ "source one" ];
  };
  missingPathsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main = { inherit patterns; };
  };
  rejects = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  unsafeArgsProject =
    gritArgs:
    api.configure {
      inherit system;
      src = fixtureRoot + "/clean";
      lint.grit.profiles.main = {
        inherit gritArgs patterns;
        paths = [ "source one" ];
      };
    };
  rejectsEmptyProfiles = rejects (builtins.attrNames emptyProfilesProject.checks);
  rejectsMissingPatterns = rejects missingPatternsProject.checks.grit-main;
  rejectsMissingPaths = rejects missingPathsProject.checks.grit-main;
  rejectsHelpArgs = rejects (unsafeArgsProject { common = [ "--help" ]; }).checks.grit-main;
  rejectsScopeArgs = rejects (unsafeArgsProject { check = [ "source-two" ]; }).checks.grit-main;

  fakeGrit = toolPkgs.writeShellApplication {
    name = "grit";
    text = ''
      : > "''${FAKE_GRIT_MARKER:?}"
      exit "''${FAKE_GRIT_STATUS:-0}"
    '';
    meta.mainProgram = "grit";
  };
  customFd = toolPkgs.writeShellApplication {
    name = "fd";
    text = ''
      : > "''${CUSTOM_FD_MARKER:?}"
      exec ${lib.getExe toolPkgs.fd} "$@"
    '';
    meta.mainProgram = "fd";
  };
  overriddenProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    toolPkgs = toolPkgs // {
      fd = customFd;
    };
    lint.grit = {
      package = fakeGrit;
      profiles.main = {
        inherit patterns;
        paths = [ "source one" ];
      };
    };
  };
in
assert rejectsEmptyProfiles;
assert rejectsMissingPatterns;
assert rejectsMissingPaths;
assert rejectsHelpArgs;
assert rejectsScopeArgs;
assert !(builtins.hasAttr "grit-main" disabledProject.checks);
assert !(builtins.hasAttr "grit-main-check" disabledProject.apps);
assert !(builtins.hasAttr "grit-main-apply" disabledProject.apps);
assert !(builtins.hasAttr "grit-rename-data" profiledProject.checks);
toolPkgs.runCommandLocal "grit-consumer"
  {
    nativeBuildInputs = with toolPkgs; [
      coreutils
      diffutils
      findutils
      gitMinimal
      gnugrep
    ];
  }
  ''
    echo 'test: clean repository and explicit outputs'
    test -e ${cleanProject.checks.grit-main}
    test -x ${cleanProject.apps.grit-main-check.program}
    test -x ${cleanProject.apps.grit-main-apply.program}

    echo 'test: named profiles preserve gates and output names'
    test -e ${profiledProject.checks.grit-policy}
    test -x ${profiledProject.apps.grit-policy-check.program}
    test -x ${profiledProject.apps.grit-policy-apply.program}
    test -x ${profiledProject.apps.grit-rename-data-check.program}
    test -x ${profiledProject.apps.grit-rename-data-apply.program}

    echo 'test: read-only violation check'
    mkdir "$TMPDIR/read-only"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/read-only/"
    chmod -R u+w "$TMPDIR/read-only"
    cd "$TMPDIR/read-only/source one"
    if ${violationProject.apps.grit-main-check.program} >"$TMPDIR/check.out" 2>&1; then
      echo 'expected the violation check to fail' >&2
      exit 1
    fi
    grep -F 'example.css' "$TMPDIR/check.out" >/dev/null
    grep -F 'example.yaml' "$TMPDIR/check.out" >/dev/null
    if grep -F 'excluded.yaml' "$TMPDIR/check.out" >/dev/null; then
      echo 'excluded path unexpectedly appeared in diagnostics' >&2
      exit 1
    fi
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    echo 'test: apps reject runtime behavior and scope overrides'
    set +e
    ${violationProject.apps.grit-main-check.program} --help >"$TMPDIR/runtime-help.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F 'runtime arguments are not supported' "$TMPDIR/runtime-help.out" >/dev/null
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    set +e
    ${violationProject.apps.grit-main-apply.program} source-two >"$TMPDIR/runtime-scope.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F 'runtime arguments are not supported' "$TMPDIR/runtime-scope.out" >/dev/null
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    echo 'test: scope ignores ambient Git ignore state'
    mkdir "$TMPDIR/ignore-scope"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/ignore-scope/"
    chmod -R u+w "$TMPDIR/ignore-scope"
    git -C "$TMPDIR/ignore-scope" init --quiet
    mkdir -p "$TMPDIR/ignore-home"
    printf '%s\n' 'example.css' >"$TMPDIR/ignore-home/global-ignore"
    HOME="$TMPDIR/ignore-home" git config --global \
      core.excludesFile "$TMPDIR/ignore-home/global-ignore"
    printf '%s\n' 'example.yaml' >"$TMPDIR/ignore-scope/.git/info/exclude"
    printf '%s\n' 'source one' >"$TMPDIR/.ignore"
    cp -R "$TMPDIR/ignore-scope" "$TMPDIR/ignore-before"

    cd "$TMPDIR/ignore-scope/source one"
    if HOME="$TMPDIR/ignore-home" \
      ${violationProject.apps.grit-main-check.program} >"$TMPDIR/ignore-check.out" 2>&1
    then
      echo 'ambient ignore state hid Grit violations' >&2
      exit 1
    fi
    grep -F 'example.css' "$TMPDIR/ignore-check.out" >/dev/null
    grep -F 'example.yaml' "$TMPDIR/ignore-check.out" >/dev/null
    diff -qr "$TMPDIR/ignore-before" "$TMPDIR/ignore-scope"

    echo 'test: explicit golden codemod'
    mkdir "$TMPDIR/apply"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/apply/"
    chmod -R u+w "$TMPDIR/apply"
    cd "$TMPDIR/apply/source one"
    ${violationProject.apps.grit-main-apply.program}
    diff -qr ${fixtureRoot + "/golden"} "$TMPDIR/apply"
    ${violationProject.apps.grit-main-check.program}

    echo 'test: package and nix-tools tool-set passthrough'
    export FAKE_GRIT_MARKER="$TMPDIR/fake-grit"
    export CUSTOM_FD_MARKER="$TMPDIR/custom-fd"
    export FAKE_GRIT_STATUS=23
    cd ${fixtureRoot + "/clean"}
    set +e
    ${overriddenProject.apps.grit-main-check.program}
    status=$?
    set -e
    test "$status" -eq 23
    test -e "$FAKE_GRIT_MARKER"
    test -e "$CUSTOM_FD_MARKER"

    touch "$out"
  ''
