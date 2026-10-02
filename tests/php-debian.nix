{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins)
    attrNames
    deepSeq
    functionArgs
    tryEval
    ;
  project =
    args:
    api.configure (
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = ./fixtures/chk/pass-php;
        treeRootFile = ".fixture-root";
        exclude = [ ".fixture-root" ];
        check.prepare = "touch .fixture-root";
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
        };
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
        };
      } args
    );
  fmt = args: lib.getExe (project { format = args; }).formatter;
  lint = args: (project { lint = args; }).apps.lint.program;
  php = project {
    format.php = true;
    lint.php = true;
  };
  debian = project {
    src = ./fixtures/chk/pass-debian;
    format.debian = true;
    lint.debian = true;
  };
  inherit (api.packagesFor toolPkgs) debputy;
  adapter = lib.getExe' debputy "debputy-nix-tools";
  test =
    name: script:
    toolPkgs.runCommandLocal name
      {
        nativeBuildInputs = [
          toolPkgs.diffutils
          toolPkgs.python3
        ];
      }
      ''
        mkdir work
        cd work
        touch .fixture-root
        export XDG_CACHE_HOME="$TMPDIR/cache"
        expect_failure() {
          if "$@" >"$TMPDIR/failure.log" 2>&1; then
            echo "unexpected success: $*" >&2
            cat "$TMPDIR/failure.log" >&2
            exit 1
          fi
          cat "$TMPDIR/failure.log"
          rm "$TMPDIR/failure.log"
        }
        ${script}
        touch "$out"
      '';
  mock = toolPkgs.writeShellScriptBin "mago" ''
    printf '%s\n' "$@" >> "$TMPDIR/mago-args"
  '';
  mockDebian = toolPkgs.writeShellScriptBin "debputy-nix-tools" ''
    printf '%s\n' "$@" >> "$TMPDIR/debian-args"
    printf '%s\n' "''${REPOCHK_EXCLUDES_JSON:-[]}" >> "$TMPDIR/debian-args"
  '';
in
{
  php-config-scope = test "php-config-scope" ''
    cp -r ${./fixtures/fmt/php/in}/. .
    chmod -R u+w .
    ${lib.getExe php.formatter}
    ${php.apps.lint.program}
    cp messy.php "$TMPDIR/default.php"
    # A changed configuration must invalidate an otherwise warm treefmt cache.
    ${fmt { php.configFile = ./conf/mago.toml; }}
    grep -q $'^\t' messy.php
    ! cmp -s messy.php "$TMPDIR/default.php"
    ${fmt { php.configFile = ./conf/mago.toml; }} --ci
    expect_failure ${lint { php.configFile = ./conf/mago.toml; }}
    ${lint { php.extraOptions = [ "--semantics" ]; }}
    printf '[invalid config' > mago.toml
    ${lint { php.extraOptions = [ "--semantics" ]; }}
    rm mago.toml
    mkdir -p global phase language
    for dir in global phase language; do printf '<?php broken {' > "$dir/bad.inc"; done
    ${(project {
      exclude = [
        ".fixture-root"
        "global"
      ];
      lint.exclude = [ "phase" ];
      lint.php = {
        exclude = [ "language" ];
        extraOptions = [ "--semantics" ];
      };
    }).apps.lint.program
    }
    cp ${./fixtures/fmt/php/in/messy.php} extension.module
    chmod u+w extension.module
    ${
      fmt {
        exclude = [
          "global/**"
          "phase/**"
          "language/**"
        ];
        php.includes = [
          "*.php"
          "*.inc"
          "*.module"
        ];
      }
    } --no-cache
    ${lint {
      php = {
        includes = [ "*.module" ];
        extraOptions = [ "--semantics" ];
        batch = false;
      };
    }}
    cp ${./fixtures/chk/fail-php/broken.inc} broken.inc
    cp broken.inc "$TMPDIR/original.inc"
    expect_failure ${
      fmt {
        php.includes = [ "broken.inc" ];
        onUnmatched = "info";
      }
    } --no-cache
    cmp broken.inc "$TMPDIR/original.inc"
  '';

  php-overrides = test "php-overrides" ''
    printf '<?php echo 1;' > one.php
    printf '<?php echo 2;' > 'two files.inc'
    ${
      fmt {
        php = {
          package = mock;
          priority = 7;
          extraOptions = [ "--extra" ];
        };
      }
    } --no-cache
    grep -F -- '--config' "$TMPDIR/mago-args"
    grep -F -- '--extra' "$TMPDIR/mago-args"
    ${
      fmt {
        php = {
          exe = lib.getExe mock;
          package = throw "exe must win";
          options = [ "replacement" ];
        };
      }
    } --no-cache
    grep -Fx replacement "$TMPDIR/mago-args"
    rm "$TMPDIR/mago-args"
    ${lint {
      php = {
        package = mock;
        extraOptions = [ "--extra" ];
      };
    }}
    test "$(grep -c '^lint$' "$TMPDIR/mago-args")" = 1
    grep -F 'two files.inc' "$TMPDIR/mago-args"
    rm "$TMPDIR/mago-args"
    ${lint {
      php = {
        exe = lib.getExe mock;
        package = throw "exe must win";
        batch = false;
      };
    }}
    test "$(grep -c '^lint$' "$TMPDIR/mago-args")" = 2
  '';

  debian-scope = test "debian-scope" ''
    cp -r ${./fixtures/fmt/debian/in}/. .
    chmod -R u+w .
    cp debian/control "$TMPDIR/control"
    cp debian/copyright "$TMPDIR/copyright"
    cp debian/tests/control "$TMPDIR/tests-control"
    cp debian/watch "$TMPDIR/watch"
    chmod 751 debian/control
    ${
      fmt {
        debian = {
          includes = [ "debian/control" ];
        };
        exclude = [
          "debian/copyright"
          "debian/tests/**"
          "debian/watch"
        ];
      }
    } --no-cache
    ! cmp -s debian/control "$TMPDIR/control"
    python3 -c 'import os, stat; assert stat.S_IMODE(os.stat("debian/control").st_mode) == 0o751'
    cmp debian/copyright "$TMPDIR/copyright"
    cmp debian/tests/control "$TMPDIR/tests-control"
    cmp debian/watch "$TMPDIR/watch"
    ${fmt { debian = true; }} --no-cache
    ${fmt { debian = true; }} --ci
    # Neither a private user config nor an excluded file may poison the run.
    mkdir -p "$TMPDIR/user/debputy"
    cp ${./fixtures/debian-invalid/config.yaml} "$TMPDIR/user/debputy/debputy-config.yaml"
    XDG_CONFIG_DIR="$TMPDIR/user" ${fmt { debian = true; }} --ci
    expect_failure ${fmt { debian.configFile = ./fixtures/debian-invalid/config.yaml; }} --no-cache
    ${fmt { debian.configFile = ./conf/debputy.yaml; }} --ci
    cp -r ${./fixtures/chk/pass-debian}/. .
    chmod -R u+w .
    ${
      fmt {
        debian = true;
        exclude = [
          "debian/changelog"
          "debian/rules"
        ];
      }
    } --no-cache
    ${debian.apps.lint.program}
    cp -r debian "$TMPDIR/before-lint"
    ${debian.apps.lint.program}
    diff -r debian "$TMPDIR/before-lint"
    sed -i 's/Thu, /Bad, /' debian/changelog
    expect_failure ${debian.apps.lint.program}
    ${lint { debian.exclude = [ "debian/changelog" ]; }}
    ${lint {
      exclude = [ "debian/changelog" ];
      debian = true;
    }}
    ${(project {
      exclude = [
        ".fixture-root"
        "debian/changelog"
      ];
      lint.debian = true;
    }).apps.lint.program
    }
    # Excluded control remains readable context, but cannot emit its own errors.
    cp ${./fixtures/chk/fail-debian/debian/control} debian/control
    ${lint {
      debian.exclude = [
        "debian/control"
        "debian/changelog"
      ];
    }}
    ${lint { debian.exclude = [ "debian" ]; }}
    cp ${./fixtures/chk/pass-debian/debian/control} debian/control
    rm debian/changelog
    expect_failure ${debian.apps.lint.program}
    ${lint { debian.exclude = [ "debian/changelog" ]; }}
    mkdir -p nested/deep
    cd nested/deep
    ${lint {
      debian.exclude = [
        "debian/control"
        "debian/changelog"
      ];
    }}
  '';

  debian-unreadable-directory = test "debian-unreadable-directory" ''
    cp -r ${./fixtures/chk/pass-debian}/debian .
    chmod -R u+w debian
    mkdir locked
    printf 'hidden source' > locked/source
    trap 'chmod 700 locked' EXIT
    chmod 000 locked
    for operation in lint coverage; do
      if ${adapter} "$operation" > "$TMPDIR/result" 2> "$TMPDIR/error"; then exit 1; fi
      cat "$TMPDIR/error"
      grep -q 'file discovery failed' "$TMPDIR/error"
      grep -q 'Permission denied' "$TMPDIR/error"
      test ! -s "$TMPDIR/result"
    done
    expect_failure ${debian.apps.lint.program}
    ${lint {
      debian.exclude = [
        "locked"
        "--"
        "-skip"
      ];
    }}
    REPOCHK_EXCLUDES_JSON='["locked", "--", "-skip"]' ${adapter} coverage > "$TMPDIR/selected.json"
    python - "$TMPDIR/selected.json" <<'PYTEST'
    import json, sys
    assert 'debian/control' in json.load(open(sys.argv[1]))
    PYTEST
    chmod 700 locked
    ${debian.apps.lint.program}
  '';

  debian-failure-safety = test "debian-failure-safety" ''
    ${lint { debian = true; }}
    mkdir debian
    cp ${./fixtures/fmt/debian/in/debian/control} debian/control
    printf 'this is not deb822\n' > debian/copyright
    cp -r debian "$TMPDIR/before"
    expect_failure ${fmt { debian = true; }} --no-cache
    diff -r debian "$TMPDIR/before"
    expect_failure ${adapter} reformat -- --unsupported
    printf 'unrelated data' > debian/control.tmp
    expect_failure ${adapter} reformat --auto-fix --style=black -- debian/control
    test "$(cat debian/control.tmp)" = 'unrelated data'
    rm debian/control.tmp
    ln -s "$TMPDIR/must-not-exist" debian/control.tmp
    expect_failure ${adapter} reformat --auto-fix --style=black -- debian/control
    test ! -e "$TMPDIR/must-not-exist"
    expect_failure ${adapter} lint --auto-fix
    expect_failure ${adapter} lint --report-output changed.xml
    test ! -e changed.xml
    rm debian/copyright debian/control.tmp
    cp debian/control debian/tests-control
    rm debian/control
    mkdir debian/tests
    mv debian/tests-control debian/tests/control
    expect_failure ${fmt { debian = true; }} --no-cache
  '';

  debian-overrides = test "debian-overrides" ''
    mkdir debian
    cp ${./fixtures/fmt/debian/in/debian/control} debian/control
    ${
      fmt {
        debian = {
          package = mockDebian;
          priority = 3;
          extraOptions = [ "--style=black" ];
        };
      }
    } --no-cache
    grep -F debputy.yaml "$TMPDIR/debian-args"
    grep -Fx debian/control "$TMPDIR/debian-args"
    ${
      fmt {
        debian = {
          exe = lib.getExe mockDebian;
          package = throw "exe must win";
          options = [ "reformat" ];
        };
      }
    } --no-cache
    ${lint {
      debian = {
        package = mockDebian;
        configFile = ./conf/debputy.yaml;
        extraOptions = [ "--no-warn-about-check-manifest" ];
        exclude = [ "debian/watch" ];
      };
    }}
    grep -F -- '--no-warn-about-check-manifest' "$TMPDIR/debian-args"
    grep -F debian/watch "$TMPDIR/debian-args"
    ${lint {
      debian = {
        exe = lib.getExe mockDebian;
        package = throw "exe must win";
      };
    }}
    # A different config must invoke the adapter even after a warm formatter run.
    before=$(wc -l < "$TMPDIR/debian-args")
    ${fmt {
      debian = {
        package = mockDebian;
        configFile = ./conf/debputy.yaml;
      };
    }}
    test "$(wc -l < "$TMPDIR/debian-args")" -gt "$before"
  '';

  php-debian-schema =
    let
      fails = args: !(tryEval (deepSeq (project args).apps true)).success;
      schemas = import ../lib/options.nix { inherit lib; };
      poisoned = toolPkgs // {
        mago = throw "disabled PHP reached Mago";
        callPackage = throw "disabled Debian reached its package";
      };
      lazy = project { toolPkgs = poisoned; };
      otherPkgs = toolPkgs // {
        callPackage = _: _: mockDebian;
      };
      debianChecker =
        (import ../lib/checker.nix {
          inherit lib;
          toolPkgsFor = _: toolPkgs;
        })
          {
            inherit system toolPkgs;
            debian = true;
          };
    in
    assert (lib.head debian.formatter.selection.formatters.debian.options) == adapter;
    assert (lib.head debianChecker.selection.projectCheckers).command == adapter;
    assert (api.packagesFor otherPkgs).debputy.outPath == mockDebian.outPath;
    assert
      attrNames (functionArgs schemas.debianCheckerOptions) == [
        "configFile"
        "enable"
        "exclude"
        "exe"
        "extraOptions"
        "package"
      ];
    assert fails {
      format.php = {
        options = [ ];
        configFile = ./conf/mago.toml;
      };
    };
    assert fails {
      format.debian = {
        options = [ ];
        configFile = ./conf/debputy.yaml;
      };
    };
    assert fails {
      lint = {
        debian = true;
        extraCheckers.debian = {
          command = "false";
          includes = [ "*" ];
        };
      };
    };
    test "php-debian-schema" ''
      test -x ${lazy.apps.lint.program}
      test -x ${lib.getExe lazy.formatter}
      ${lint {
        debian = true;
        extraProjectCheckers.debian.command = toolPkgs.writeShellScript "replacement" "exit 0";
      }}
    '';

  php-sandbox-lint = php.checks.linting;
  debian-sandbox-lint = debian.checks.linting;
}
