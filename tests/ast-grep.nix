{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  fixtureRoot = ./fixtures/ast-grep;
  configFile = ".config/ast-grep/sgconfig.yml";

  configure =
    src:
    api.configure {
      inherit src system;
      exclude = [ "source-two/excluded.js" ];
      lint.ast-grep.profiles = {
        javascript = {
          inherit configFile;
          paths = [
            "source one"
            "source-two"
          ];
        };
        rust = {
          inherit configFile;
          paths = [ "rust code" ];
        };
      };
    };

  cleanProject = configure (fixtureRoot + "/clean");
  violationProject = configure (fixtureRoot + "/violation");
  disabledProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
  };

  rejects = value: !(builtins.tryEval value).success;
  emptyProfilesProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep = true;
  };
  missingConfigProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles.policy.paths = [ "." ];
  };
  missingPathsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles.policy = { inherit configFile; };
  };
  invalidNameProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles."Not Valid" = {
      inherit configFile;
      paths = [ "." ];
    };
  };
  invalidGateProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles.policy = {
      inherit configFile;
      paths = [ "." ];
      gate = "yes";
    };
  };
  unsafeExcludeProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles.policy = {
      inherit configFile;
      paths = [ "." ];
      exclude = [ "!source-two/excluded.js" ];
    };
  };
  ungatedProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    lint.ast-grep.profiles.codemod = {
      inherit configFile;
      paths = [ "source one" ];
      gate = false;
    };
  };
  fakeAstGrep =
    name: markerVariable:
    toolPkgs.writeShellApplication {
      inherit name;
      text = ''
        marker_variable=${lib.escapeShellArg markerVariable}
        marker=''${!marker_variable:-}
        if [[ -n $marker ]]; then
          : > "$marker"
        fi
        if [[ -n ''${FAKE_AST_GREP_ARGS:-} ]]; then
          printf '%s\n' "$@" > "$FAKE_AST_GREP_ARGS"
        fi
        for arg in "$@"; do
          if [[ $arg == --update-all && -n ''${FAKE_AST_GREP_APPLY_MARKER:-} ]]; then
            : > "$FAKE_AST_GREP_APPLY_MARKER"
          fi
        done
        echo ${lib.escapeShellArg name} >&2
        exit "''${FAKE_AST_GREP_STATUS:-0}"
      '';
      meta.mainProgram = name;
    };

  toolPkgsAstGrep = fakeAstGrep "toolpkgs-ast-grep" "FAKE_TOOLPKGS_MARKER";
  packageAstGrep = fakeAstGrep "package-ast-grep" "FAKE_PACKAGE_MARKER";
  fakeProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    toolPkgs = toolPkgs // {
      ast-grep = toolPkgsAstGrep;
    };
    lint.ast-grep = {
      package = packageAstGrep;
      profiles.policy = {
        inherit configFile;
        paths = [ "source one" ];
      };
    };
  };
  toolPkgsProject = api.configure {
    inherit system;
    src = fixtureRoot + "/clean";
    toolPkgs = toolPkgs // {
      ast-grep = toolPkgsAstGrep;
    };
    lint.ast-grep.profiles.policy = {
      inherit configFile;
      paths = [ "source one" ];
    };
  };
in
assert rejects (builtins.attrNames emptyProfilesProject.checks);
assert rejects missingConfigProject.checks.ast-grep-policy;
assert rejects missingPathsProject.checks.ast-grep-policy;
assert rejects (builtins.attrNames invalidNameProject.checks);
assert rejects invalidGateProject.checks.ast-grep-policy;
assert rejects unsafeExcludeProject.checks.ast-grep-policy;
assert !(builtins.hasAttr "ast-grep-javascript" disabledProject.checks);
assert !(builtins.hasAttr "ast-grep-javascript-check" disabledProject.apps);
assert !(builtins.hasAttr "ast-grep-javascript-apply" disabledProject.apps);
assert !(builtins.hasAttr "ast-grep-codemod" ungatedProject.checks);
toolPkgs.runCommandLocal "ast-grep-consumer"
  {
    nativeBuildInputs = with toolPkgs; [
      coreutils
      diffutils
      gitMinimal
      gnugrep
    ];
  }
  ''
    echo 'test: clean repository and named outputs'
    test -e ${cleanProject.checks.ast-grep-javascript}
    test -e ${cleanProject.checks.ast-grep-rust}
    test -x ${cleanProject.apps.ast-grep-javascript-check.program}
    test -x ${cleanProject.apps.ast-grep-javascript-apply.program}
    test -x ${cleanProject.apps.ast-grep-rust-check.program}
    test -x ${cleanProject.apps.ast-grep-rust-apply.program}
    test -x ${ungatedProject.apps.ast-grep-codemod-check.program}
    test -x ${ungatedProject.apps.ast-grep-codemod-apply.program}

    echo 'test: read-only diagnostics, exclusions, spaces, and root discovery'
    mkdir "$TMPDIR/read-only"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/read-only/"
    chmod -R u+w "$TMPDIR/read-only"
    cd "$TMPDIR/read-only/source one"
    if ${violationProject.apps.ast-grep-javascript-check.program} >"$TMPDIR/javascript.out" 2>&1; then
      echo 'expected the JavaScript profile to fail' >&2
      exit 1
    fi
    grep -F 'first.js' "$TMPDIR/javascript.out" >/dev/null
    grep -F 'second.js' "$TMPDIR/javascript.out" >/dev/null
    if grep -F 'excluded.js' "$TMPDIR/javascript.out" >/dev/null; then
      echo 'excluded path unexpectedly appeared in diagnostics' >&2
      exit 1
    fi
    if ${violationProject.apps.ast-grep-rust-check.program} >"$TMPDIR/rust.out" 2>&1; then
      echo 'expected the Rust profile to fail' >&2
      exit 1
    fi
    grep -F 'main.rs' "$TMPDIR/rust.out" >/dev/null
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    echo 'test: apps reject runtime scope and behavior overrides'
    if ${violationProject.apps.ast-grep-javascript-check.program} --max-results=0 >"$TMPDIR/unsafe.out" 2>&1; then
      echo 'check app accepted a runtime argument' >&2
      exit 1
    fi
    grep -F 'runtime arguments are not supported' "$TMPDIR/unsafe.out" >/dev/null
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    if ${violationProject.apps.ast-grep-javascript-apply.program} source-two >"$TMPDIR/scope.out" 2>&1; then
      echo 'apply app accepted an alternate target path' >&2
      exit 1
    fi
    grep -F 'runtime arguments are not supported' "$TMPDIR/scope.out" >/dev/null
    diff -qr ${fixtureRoot + "/violation"} "$TMPDIR/read-only"

    echo 'test: scope ignores user-global and checkout-private ignore state'
    mkdir "$TMPDIR/ignore-scope"
    cp -R ${fixtureRoot + "/clean"}/. "$TMPDIR/ignore-scope/"
    chmod -R u+w "$TMPDIR/ignore-scope"
    printf '%s\n' 'const first = legacyCall(value);' >"$TMPDIR/ignore-scope/source one/first.js"
    printf '%s\n' 'const second = legacyCall(value);' >"$TMPDIR/ignore-scope/source-two/second.js"
    printf '%s\n' 'fn main() {' '    let value = legacy_call(input);' '}' \
      >"$TMPDIR/ignore-scope/rust code/main.rs"
    git -C "$TMPDIR/ignore-scope" init --quiet
    printf '%s\n' 'second.js' >"$TMPDIR/ignore-scope/.git/info/exclude"
    mkdir -p "$TMPDIR/ignore-home/.config/git"
    printf '%s\n' 'first.js' >"$TMPDIR/ignore-home/.config/git/ignore"
    printf '%s\n' 'main.rs' >"$TMPDIR/.ignore"
    cp -R "$TMPDIR/ignore-scope" "$TMPDIR/ignore-before"

    cd "$TMPDIR/ignore-scope/source one"
    if HOME="$TMPDIR/ignore-home" \
      ${cleanProject.apps.ast-grep-javascript-check.program} >"$TMPDIR/ignore-javascript.out" 2>&1
    then
      echo 'user ignore state hid JavaScript violations' >&2
      exit 1
    fi
    grep -F 'first.js' "$TMPDIR/ignore-javascript.out" >/dev/null
    grep -F 'second.js' "$TMPDIR/ignore-javascript.out" >/dev/null

    if HOME="$TMPDIR/ignore-home" \
      ${cleanProject.apps.ast-grep-rust-check.program} >"$TMPDIR/ignore-rust.out" 2>&1
    then
      echo 'parent ignore state hid a Rust violation' >&2
      exit 1
    fi
    grep -F 'main.rs' "$TMPDIR/ignore-rust.out" >/dev/null
    diff -qr "$TMPDIR/ignore-before" "$TMPDIR/ignore-scope"

    echo 'test: explicit profile applies match golden output'
    mkdir "$TMPDIR/apply"
    cp -R ${fixtureRoot + "/violation"}/. "$TMPDIR/apply/"
    chmod -R u+w "$TMPDIR/apply"
    cd "$TMPDIR/apply/source-two"
    ${violationProject.apps.ast-grep-javascript-apply.program}
    ${violationProject.apps.ast-grep-rust-apply.program}
    diff -qr ${fixtureRoot + "/golden"} "$TMPDIR/apply"
    ${violationProject.apps.ast-grep-javascript-check.program}
    ${violationProject.apps.ast-grep-rust-check.program}

    echo 'test: toolPkgs default and explicit package override'
    export FAKE_AST_GREP_ARGS="$TMPDIR/args"
    export FAKE_AST_GREP_STATUS=23
    export FAKE_TOOLPKGS_MARKER="$TMPDIR/toolpkgs"
    cd ${fixtureRoot + "/clean"}
    set +e
    ${toolPkgsProject.apps.ast-grep-policy-check.program} >"$TMPDIR/toolpkgs.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 23
    test -e "$FAKE_TOOLPKGS_MARKER"
    grep -F 'toolpkgs-ast-grep' "$TMPDIR/toolpkgs.out" >/dev/null
    grep -Fx -- '--error' "$FAKE_AST_GREP_ARGS" >/dev/null

    unset FAKE_TOOLPKGS_MARKER
    export FAKE_PACKAGE_MARKER="$TMPDIR/package"
    export FAKE_AST_GREP_APPLY_MARKER="$TMPDIR/apply-marker"
    set +e
    ${fakeProject.apps.ast-grep-policy-check.program} >"$TMPDIR/package.out" 2>&1
    status=$?
    set -e
    test "$status" -eq 23
    test -e "$FAKE_PACKAGE_MARKER"
    test ! -e "$FAKE_AST_GREP_APPLY_MARKER"
    grep -F 'package-ast-grep' "$TMPDIR/package.out" >/dev/null

    export FAKE_AST_GREP_STATUS=0
    ${fakeProject.apps.ast-grep-policy-apply.program}
    test -e "$FAKE_AST_GREP_APPLY_MARKER"

    touch "$out"
  ''
