{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins) hasAttr;

  fixtureRoot = ./fixtures/grit;
  policyPatterns = fixtureRoot + "/policy";
  codemodPatterns = fixtureRoot + "/codemods";
  brokenPatterns = fixtureRoot + "/broken";

  configure =
    src:
    api.configure {
      inherit src system;
      format.exclude = [
        "*.css"
        "*.yaml"
      ];
      lint = {
        exclude = [ "source-two/excluded.yaml" ];
        grit.profiles = {
          policy = {
            patterns = policyPatterns;
            paths = [ "source one" ];
          };
          codemod = {
            patterns = codemodPatterns;
            paths = [
              "source one"
              "source-two"
            ];
            gate = false;
          };
        };
      };
    };

  cleanProject = configure (fixtureRoot + "/clean");
  violationProject = configure (fixtureRoot + "/violation");
  aggregateViolationProject = api.configure {
    inherit system;
    src = fixtureRoot + "/violation";
    format.exclude = [
      "*.css"
      "*.yaml"
    ];
    lint = {
      exclude = [ "source-two/excluded.yaml" ];
      grit.profiles = {
        css-policy = {
          patterns = policyPatterns;
          paths = [ "source one" ];
        };
        ungated = {
          patterns = codemodPatterns;
          paths = [ "source one" ];
          gate = false;
        };
        yaml-policy = {
          patterns = codemodPatterns;
          paths = [ "source-two" ];
        };
      };
    };
  };
  brokenProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.broken = {
      patterns = brokenPatterns;
      paths = [ "source one" ];
      gate = false;
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
    lint.grit.profiles.main.patterns = policyPatterns;
  };
  singlePatternProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main = {
      patterns = fixtureRoot + "/markdown-patterns/policy.md";
      paths = [ "source one" ];
    };
  };
  nativePatternsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main = {
      patterns = fixtureRoot + "/patterns";
      paths = [ "source one" ];
    };
  };
  globPathProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main = {
      patterns = policyPatterns;
      paths = [ "source-*" ];
    };
  };
  unsafeArgsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit.profiles.main = {
      patterns = policyPatterns;
      paths = [ "source one" ];
      gritArgs.check = [ "source-two" ];
    };
  };
  rejects = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  rejectsEmptyProfiles = rejects (builtins.attrNames emptyProfilesProject.checks);
  rejectsMissingPatterns = rejects (builtins.attrNames missingPatternsProject.checks);
  rejectsMissingPaths = rejects (builtins.attrNames missingPathsProject.checks);
  rejectsSinglePattern = rejects (builtins.attrNames singlePatternProject.checks);
  rejectsNativePatterns = rejects (builtins.attrNames nativePatternsProject.checks);
  rejectsGlobPath = rejects (builtins.attrNames globPathProject.checks);
  rejectsScopeArgs = rejects (builtins.attrNames unsafeArgsProject.checks);

  fakeGrit = toolPkgs.writeShellApplication {
    name = "grit";
    text = ''
      printf '%q ' "$@" >> "''${FAKE_GRIT_MARKER:?}"
      printf '\n' >> "$FAKE_GRIT_MARKER"
      exit "''${FAKE_GRIT_STATUS:-0}"
    '';
    meta.mainProgram = "grit";
  };
  customFd = toolPkgs.writeShellApplication {
    name = "fd";
    text = ''
      printf '%s\n' "$@" > "''${CUSTOM_FD_MARKER:?}"
      if [[ -n ''${CUSTOM_FD_STATUS:-} ]]; then
        exit "$CUSTOM_FD_STATUS"
      fi
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
        patterns = policyPatterns;
        paths = [ "source one" ];
      };
    };
  };
  overlappingProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.grit = {
      package = fakeGrit;
      profiles.main = {
        patterns = policyPatterns;
        paths = [
          "."
          "source one"
        ];
        gate = false;
      };
    };
  };
  noApplyProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    format.exclude = [
      "*.css"
      "*.yaml"
    ];
    lint.grit = {
      package = fakeGrit;
      profiles.main = {
        patterns = policyPatterns;
        paths = [ "source one" ];
      };
    };
  };
  noApplyFormatProject = api.configure {
    inherit system;
    src = ./fixtures/fmt/grit/in;
    treeRootFile = "formatted.grit";
    format.grit = {
      package = fakeGrit;
    };
  };
  consumerBuild = toolPkgs.runCommandLocal "consumer-rust-build-must-not-run" { } ''
    echo 'the aggregate app pulled in the consumer build closure' >&2
    exit 99
  '';
  isolatedProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    validate.runtimeInputs = [ consumerBuild ];
    lint.grit.profiles.main = {
      patterns = policyPatterns;
      paths = [ "source one" ];
    };
  };
  aggregateClosure = toolPkgs.closureInfo {
    rootPaths = [ isolatedProject.apps.grit-check.program ];
  };
in
assert rejectsEmptyProfiles;
assert rejectsMissingPatterns;
assert rejectsMissingPaths;
assert rejectsSinglePattern;
assert rejectsNativePatterns;
assert rejectsGlobPath;
assert rejectsScopeArgs;
assert !(hasAttr "grit-policy" disabledProject.checks);
assert !(hasAttr "grit-policy-test" disabledProject.checks);
assert !(hasAttr "grit-policy-check" disabledProject.apps);
assert !(hasAttr "grit-policy-apply" disabledProject.apps);
assert !(hasAttr "grit-policy-test" disabledProject.apps);
assert !(hasAttr "grit-check" disabledProject.apps);
assert hasAttr "grit-policy" cleanProject.checks;
assert hasAttr "grit-policy-test" cleanProject.checks;
assert hasAttr "grit-codemod-test" cleanProject.checks;
assert !(hasAttr "grit-codemod" cleanProject.checks);
assert !(hasAttr "grit-check" cleanProject.checks);
assert hasAttr "grit-check" cleanProject.apps;
toolPkgs.runCommandLocal "grit-consumer"
  {
    nativeBuildInputs = with toolPkgs; [
      coreutils
      diffutils
      findutils
      gitMinimal
      gnugrep
      util-linux
    ];
  }
  ''
    echo 'test: embedded pattern tests are automatic for every profile'
    test -e ${cleanProject.checks.grit-policy-test}
    test -e ${cleanProject.checks.grit-codemod-test}
    ${cleanProject.apps.grit-policy-test.program} >"$TMPDIR/policy-test.out" 2>&1
    grep -F 'Found 1 testable patterns.' "$TMPDIR/policy-test.out" >/dev/null
    grep -F 'All 2 samples passed.' "$TMPDIR/policy-test.out" >/dev/null
    ${cleanProject.apps.grit-codemod-test.program} >"$TMPDIR/codemod-test.out" 2>&1
    grep -F 'Found 2 testable patterns.' "$TMPDIR/codemod-test.out" >/dev/null
    grep -F 'All 4 samples passed.' "$TMPDIR/codemod-test.out" >/dev/null

    echo 'test: passing aggregate app has one concise result'
    cd ${fixtureRoot + "/clean"}
    ${cleanProject.apps.grit-check.program} >"$TMPDIR/aggregate-clean.out" 2>&1
    grep -Fx '[grit:policy:test] passed' "$TMPDIR/aggregate-clean.out" >/dev/null
    grep -Fx '[grit:policy:check] passed' "$TMPDIR/aggregate-clean.out" >/dev/null
    grep -Fx '[grit:codemod:test] passed' "$TMPDIR/aggregate-clean.out" >/dev/null
    test "$(grep -c '^\[grit:policy:test\]' "$TMPDIR/aggregate-clean.out")" -eq 1
    test "$(grep -c '^\[grit:policy:check\]' "$TMPDIR/aggregate-clean.out")" -eq 1
    test "$(grep -c '^\[grit:codemod:test\]' "$TMPDIR/aggregate-clean.out")" -eq 1
    ! grep -F '[grit:codemod:check]' "$TMPDIR/aggregate-clean.out" >/dev/null
    test "$(grep -c '^grit-check:' "$TMPDIR/aggregate-clean.out")" -eq 1
    grep -Fx 'grit-check: ok (profile tests: 2; gated scans: 1)' \
      "$TMPDIR/aggregate-clean.out" >/dev/null

    echo 'test: all failing gated profiles run with one copy of each diagnostic'
    mkdir "$TMPDIR/aggregate-violation"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/aggregate-violation/"
    chmod -R u+w "$TMPDIR/aggregate-violation"
    cp -R "$TMPDIR/aggregate-violation" "$TMPDIR/aggregate-violation-before"
    cd "$TMPDIR/aggregate-violation"
    set +e
    ${aggregateViolationProject.apps.grit-check.program} >"$TMPDIR/aggregate-fail.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 1
    grep -F '[grit:css-policy:check]' "$TMPDIR/aggregate-fail.out" | \
      grep -F 'example.css' >/dev/null
    grep -F '[grit:yaml-policy:check]' "$TMPDIR/aggregate-fail.out" | \
      grep -F 'example.yaml' >/dev/null
    grep -F '[grit:css-policy:check]' "$TMPDIR/aggregate-fail.out" | \
      grep -F '2:5' | grep -F 'match' | \
      grep -F 'Legacy colors bypass the approved design-token palette.' | \
      grep -F 'no_legacy_css' >/dev/null
    grep -F '[grit:yaml-policy:check]' "$TMPDIR/aggregate-fail.out" | \
      grep -F '1:1' | grep -F 'rewrite' | \
      grep -F 'Replace the legacy data key while preserving its value and surrounding comments.' | \
      grep -F 'replace_legacy_yaml' >/dev/null
    grep -Fx '[grit:ungated:test] passed' "$TMPDIR/aggregate-fail.out" >/dev/null
    ! grep -F '[grit:ungated:check]' "$TMPDIR/aggregate-fail.out" >/dev/null
    ! grep -E '^\[grit:[^]]+\] $' "$TMPDIR/aggregate-fail.out" >/dev/null
    test "$(grep -Fc 'example.css' "$TMPDIR/aggregate-fail.out")" -eq 1
    test "$(grep -Fc 'example.yaml' "$TMPDIR/aggregate-fail.out")" -eq 1
    test "$(grep -c '^grit-check:' "$TMPDIR/aggregate-fail.out")" -eq 1
    grep -Fx \
      'grit-check: failed (policy failures: 2; pattern-test failures: 0; runner errors: 0)' \
      "$TMPDIR/aggregate-fail.out" >/dev/null
    diff -qr "$TMPDIR/aggregate-violation-before" "$TMPDIR/aggregate-violation"

    echo 'test: a wrong embedded expectation fails read-only'
    pattern_before=$(sha256sum ${brokenPatterns + "/wrong_rewrite.md"})
    set +e
    ${brokenProject.apps.grit-broken-test.program} >"$TMPDIR/broken-test.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F 'wrong_rewrite' "$TMPDIR/broken-test.out" >/dev/null
    grep -F 'Rejects an incorrect expected rewrite' "$TMPDIR/broken-test.out" >/dev/null
    test "$pattern_before" = "$(sha256sum ${brokenPatterns + "/wrong_rewrite.md"})"

    set +e
    ${brokenProject.apps.lint.program} >"$TMPDIR/broken-lint.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F '[grit:broken:test]' "$TMPDIR/broken-lint.out" >/dev/null
    grep -F 'wrong_rewrite' "$TMPDIR/broken-lint.out" >/dev/null
    grep -Fx \
      'grit-check: failed (policy failures: 0; pattern-test failures: 1; runner errors: 0)' \
      "$TMPDIR/broken-lint.out" >/dev/null
    test "$pattern_before" = "$(sha256sum ${brokenPatterns + "/wrong_rewrite.md"})"

    set +e
    ${brokenProject.apps.validate.program} >"$TMPDIR/broken-validate.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F '[grit:broken:test]' "$TMPDIR/broken-validate.out" >/dev/null
    grep -F 'wrong_rewrite' "$TMPDIR/broken-validate.out" >/dev/null
    test "$pattern_before" = "$(sha256sum ${brokenPatterns + "/wrong_rewrite.md"})"

    set +e
    ${cleanProject.apps.grit-policy-test.program} --update >"$TMPDIR/test-args.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F 'runtime arguments are not supported' "$TMPDIR/test-args.out" >/dev/null

    echo 'test: clean repository and explicit outputs'
    test -e ${cleanProject.checks.grit-policy}
    test -x ${cleanProject.apps.grit-policy-check.program}
    test -x ${cleanProject.apps.grit-policy-apply.program}
    test -x ${cleanProject.apps.grit-codemod-check.program}
    test -x ${cleanProject.apps.grit-codemod-apply.program}
    cd ${fixtureRoot + "/clean"}
    ${cleanProject.apps.grit-policy-check.program}
    ${cleanProject.apps.grit-codemod-check.program}

    echo 'test: normal lint gates policies and every profile pattern test'
    mkdir "$TMPDIR/clean-gate"
    cp -R ${fixtureRoot + "/clean"}/. "$TMPDIR/clean-gate/"
    chmod -R u+w "$TMPDIR/clean-gate"
    cd "$TMPDIR/clean-gate"
    ${cleanProject.apps.lint.program} >"$TMPDIR/clean-lint.out" 2>&1
    grep -Fx '[grit:policy:test] passed' "$TMPDIR/clean-lint.out" >/dev/null
    grep -Fx '[grit:codemod:test] passed' "$TMPDIR/clean-lint.out" >/dev/null
    grep -Fx '[grit:policy:check] passed' "$TMPDIR/clean-lint.out" >/dev/null
    ! grep -F '[grit:codemod:check]' "$TMPDIR/clean-lint.out" >/dev/null
    test "$(grep -c '^grit-check:' "$TMPDIR/clean-lint.out")" -eq 1
    ! grep -F '=== /nix/store/' "$TMPDIR/clean-lint.out" >/dev/null

    echo 'test: match-only policy reports its explanation without modifying source'
    mkdir "$TMPDIR/policy"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/policy/"
    chmod -R u+w "$TMPDIR/policy"
    cp -R "$TMPDIR/policy" "$TMPDIR/policy-before"
    cd "$TMPDIR/policy/source one"
    set +e
    ${violationProject.apps.grit-policy-check.program} >"$TMPDIR/policy.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F 'example.css' "$TMPDIR/policy.out" >/dev/null
    grep -F 'Legacy colors bypass the approved design-token palette.' "$TMPDIR/policy.out" >/dev/null
    grep -F 'match' "$TMPDIR/policy.out" >/dev/null
    grep -F 'no_legacy_css' "$TMPDIR/policy.out" >/dev/null
    ! grep -F 'Fix available.' "$TMPDIR/policy.out" >/dev/null
    diff -qr "$TMPDIR/policy-before" "$TMPDIR/policy"

    echo 'test: codemod check covers two patterns and two scoped directories'
    cd "$TMPDIR/policy"
    set +e
    ${violationProject.apps.grit-codemod-check.program} >"$TMPDIR/codemod.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    for diagnostic in \
      example.css \
      example.yaml \
      'Replace legacy colors with the approved design-token value.' \
      'Replace the legacy data key while preserving its value and surrounding comments.' \
      replace_legacy_css \
      replace_legacy_yaml \
      'Fix available.'
    do
      grep -F "$diagnostic" "$TMPDIR/codemod.out" >/dev/null
    done
    ! grep -F 'excluded.yaml' "$TMPDIR/codemod.out" >/dev/null
    diff -qr "$TMPDIR/policy-before" "$TMPDIR/policy"

    echo 'test: ambient ignore state cannot narrow configured scope'
    mkdir "$TMPDIR/ignore-scope"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/ignore-scope/"
    chmod -R u+w "$TMPDIR/ignore-scope"
    git -C "$TMPDIR/ignore-scope" init --quiet
    mkdir -p "$TMPDIR/ignore-home"
    printf '%s\n' 'example.css' >"$TMPDIR/ignore-home/global-ignore"
    HOME="$TMPDIR/ignore-home" git config --global \
      core.excludesFile "$TMPDIR/ignore-home/global-ignore"
    printf '%s\n' 'example.yaml' >"$TMPDIR/ignore-scope/.git/info/exclude"
    printf '%s\n' 'example.css' >"$TMPDIR/ignore-scope/.ignore"
    cp -R "$TMPDIR/ignore-scope" "$TMPDIR/ignore-before"
    cd "$TMPDIR/ignore-scope"
    set +e
    HOME="$TMPDIR/ignore-home" \
      ${violationProject.apps.grit-codemod-check.program} >"$TMPDIR/ignore.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F 'example.css' "$TMPDIR/ignore.out" >/dev/null
    grep -F 'example.yaml' "$TMPDIR/ignore.out" >/dev/null
    ! grep -F 'excluded.yaml' "$TMPDIR/ignore.out" >/dev/null
    diff -qr "$TMPDIR/ignore-before" "$TMPDIR/ignore-scope"

    echo 'test: normal lint reports the gated policy without applying it'
    cd "$TMPDIR/policy/source one"
    set +e
    ${violationProject.apps.lint.program} >"$TMPDIR/lint.out" 2>&1
    status=$?
    set -e
    test "$status" -ne 0
    grep -F '[grit:policy:test] passed' "$TMPDIR/lint.out" >/dev/null
    grep -F '[grit:codemod:test] passed' "$TMPDIR/lint.out" >/dev/null
    grep -F '[grit:policy:check] policy violations found' "$TMPDIR/lint.out" >/dev/null
    ! grep -F '[grit:codemod:check]' "$TMPDIR/lint.out" >/dev/null
    test "$(grep -c '^grit-check:' "$TMPDIR/lint.out")" -eq 1
    diff -qr --exclude=.tmp "$TMPDIR/policy-before" "$TMPDIR/policy"

    echo 'test: explicit apply matches golden output'
    mkdir "$TMPDIR/apply"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/apply/"
    chmod -R u+w "$TMPDIR/apply"
    cd "$TMPDIR/apply"
    ${violationProject.apps.grit-codemod-apply.program}
    diff -qr ${fixtureRoot + "/golden"} "$TMPDIR/apply"
    ${violationProject.apps.grit-codemod-check.program}

    echo 'test: supplied Grit package and nix-tools fd are honored'
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
    test "$(tail -n 1 "$CUSTOM_FD_MARKER")" = 'source one'
    grep -Fx -- '--ignore-file' "$CUSTOM_FD_MARKER" >/dev/null

    echo 'test: selector failures retain their exit status and stop before Grit'
    export CUSTOM_FD_STATUS=47
    export FAKE_GRIT_MARKER="$TMPDIR/grit-must-not-run"
    set +e
    ${overriddenProject.apps.grit-main-check.program}
    status=$?
    set -e
    test "$status" -eq 47
    test ! -e "$FAKE_GRIT_MARKER"

    echo 'test: overlapping target roots pass every file to Grit once'
    unset CUSTOM_FD_STATUS FAKE_GRIT_STATUS
    mkdir "$TMPDIR/overlap"
    cp -R ${fixtureRoot + "/clean"}/. "$TMPDIR/overlap/"
    chmod -R u+w "$TMPDIR/overlap"
    cd "$TMPDIR/overlap"
    : > "$FAKE_GRIT_MARKER"
    ${overlappingProject.apps.grit-main-check.program}
    printf -v target_arg '%q' "$PWD/source one/example.css"
    test "$(grep -oF "$target_arg" "$FAKE_GRIT_MARKER" | wc -l)" -eq 1

    echo 'test: a missing live target is a runner failure, not a policy violation'
    mkdir "$TMPDIR/missing-target"
    cp -R ${fixtureRoot + "/clean"}/. "$TMPDIR/missing-target/"
    chmod -R u+w "$TMPDIR/missing-target"
    mv "$TMPDIR/missing-target/source one" "$TMPDIR/missing-target/source removed"
    cd "$TMPDIR/missing-target"
    : > "$FAKE_GRIT_MARKER"
    : > "$CUSTOM_FD_MARKER"
    set +e
    ${overriddenProject.apps.grit-check.program} >"$TMPDIR/missing-target.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F '[grit:main:check] runner failed with status 2' \
      "$TMPDIR/missing-target.out" >/dev/null
    grep -Fx \
      'grit-check: failed (policy failures: 0; pattern-test failures: 0; runner errors: 1)' \
      "$TMPDIR/missing-target.out" >/dev/null

    echo 'test: a missing project root marker is a runner failure'
    mkdir "$TMPDIR/no-root"
    cd "$TMPDIR/no-root"
    set +e
    ${overriddenProject.apps.grit-check.program} >"$TMPDIR/no-root.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F "could not find project root marker 'flake.nix'" "$TMPDIR/no-root.out" >/dev/null
    grep -Fx \
      'grit-check: failed (policy failures: 0; pattern-test failures: 0; runner errors: 1)' \
      "$TMPDIR/no-root.out" >/dev/null

    echo 'test: aggregate distinguishes runner failures'
    cd ${fixtureRoot + "/clean"}
    : > "$FAKE_GRIT_MARKER"
    export FAKE_GRIT_STATUS=23
    set +e
    ${overriddenProject.apps.grit-check.program} >"$TMPDIR/aggregate-runner.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 2
    grep -F '[grit:main:test] runner failed with status 23' \
      "$TMPDIR/aggregate-runner.out" >/dev/null
    grep -F '[grit:main:check] runner failed with status 23' \
      "$TMPDIR/aggregate-runner.out" >/dev/null
    grep -Fx \
      'grit-check: failed (policy failures: 0; pattern-test failures: 0; runner errors: 2)' \
      "$TMPDIR/aggregate-runner.out" >/dev/null

    echo 'test: read-only paths never pass the apply flag'
    unset FAKE_GRIT_STATUS
    : > "$FAKE_GRIT_MARKER"
    mkdir "$TMPDIR/no-apply"
    cp -R ${fixtureRoot + "/clean"}/. "$TMPDIR/no-apply/"
    chmod -R u+w "$TMPDIR/no-apply"
    cd "$TMPDIR/no-apply"
    ${noApplyProject.apps.grit-main-test.program}
    ${noApplyProject.apps.grit-main-check.program}
    ${noApplyProject.apps.grit-check.program}
    ${noApplyProject.apps.lint.program}
    ${noApplyProject.apps.validate.program}
    mkdir "$TMPDIR/format-no-apply"
    cp -R ${./fixtures/fmt/grit/in}/. "$TMPDIR/format-no-apply/"
    chmod -R u+w "$TMPDIR/format-no-apply"
    export HOME="$TMPDIR/format-home"
    mkdir "$HOME"
    cd "$TMPDIR/format-no-apply"
    ${noApplyFormatProject.apps.format.program}
    ! grep -F -- '--fix' "$FAKE_GRIT_MARKER" >/dev/null

    echo 'test: aggregate retains real Grit color on a terminal and respects NO_COLOR'
    cd "$TMPDIR/aggregate-violation"
    ! grep -F $'\033[' "$TMPDIR/aggregate-fail.out" >/dev/null
    set +e
    env -u NO_COLOR -u CLICOLOR_FORCE script --quiet --return \
      --command ${lib.escapeShellArg aggregateViolationProject.apps.grit-check.program} \
      "$TMPDIR/aggregate-color.out" >/dev/null
    status=$?
    set -e
    test "$status" -eq 1
    grep -F $'\033[2m[grit:css-policy:test]\033[0m \033[32mpassed\033[0m' \
      "$TMPDIR/aggregate-color.out" >/dev/null
    grep -F $'\033[2m[grit:css-policy:check]\033[0m \033[' \
      "$TMPDIR/aggregate-color.out" >/dev/null
    grep -F $'\033[2m[grit:yaml-policy:check]\033[0m \033[' \
      "$TMPDIR/aggregate-color.out" >/dev/null
    grep -F $'\033[2mgrit-check: failed (policy failures: 2; pattern-test failures: 0; runner errors: 0)\033[0m' \
      "$TMPDIR/aggregate-color.out" >/dev/null

    set +e
    CLICOLOR_FORCE=1 ${aggregateViolationProject.apps.grit-check.program} \
      >"$TMPDIR/aggregate-forced-color.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 1
    grep -F $'\033[2m[grit:css-policy:check]\033[0m \033[' \
      "$TMPDIR/aggregate-forced-color.out" >/dev/null
    ! grep -F ' running' "$TMPDIR/aggregate-forced-color.out" >/dev/null

    set +e
    NO_COLOR=1 CLICOLOR_FORCE=1 script --quiet --return \
      --command ${lib.escapeShellArg aggregateViolationProject.apps.grit-check.program} \
      "$TMPDIR/aggregate-no-color.out" >/dev/null
    status=$?
    set -e
    test "$status" -eq 1
    ! grep -F $'\033[' "$TMPDIR/aggregate-no-color.out" >/dev/null

    echo 'test: aggregate app excludes consumer validation build closures'
    test -e ${aggregateClosure}

    touch "$out"
  ''
