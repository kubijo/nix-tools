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
  source = ./fixtures/basedpyright;
  python = toolPkgs.python3.withPackages (p: [ p.packaging ]);
  project =
    args:
    api.configure (
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = source;
        treeRootFile = ".fixture-root";
        check.prepare = "touch .fixture-root";
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          onUnmatched = "info";
          extraFormatters.identity = {
            command = "${toolPkgs.coreutils}/bin/true";
            includes = [ "*" ];
            cacheInputs = [ ];
          };
        };
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
          basedpyright.projects = {
            alpha = {
              configFile = "alpha/pyproject.toml";
              inherit python;
            };
            beta = {
              configFile = "beta/pyproject.toml";
              inherit python;
            };
          };
        };
      } args
    );
  configured = project { };
  rich = project {
    lint.basedpyright.projects.alpha.reporter = "rich";
    lint.basedpyright.projects.beta.reporter = "rich";
  };
  lint = args: (project { lint.basedpyright.projects.alpha = args; }).apps.lint.program;
  mock = toolPkgs.writeShellScriptBin "basedpyright" ''
    set -eu
    [ "$#" -eq 4 ]
    [ "$1" = --project ]
    [ "$2" = "$PWD/pyproject.toml" ]
    [ "$3" = --pythonpath ]
    [ "$4" = ${python}/bin/python ]
    [ -f src/app.py ]
    [ -z "''${PYTHONPATH:-}" ]
    [ -z "''${PYTHONHOME:-}" ]
    [ -z "''${VIRTUAL_ENV:-}" ]
    printf 'called\n' >> "$TMPDIR/basedpyright-calls"
  '';
  failure = toolPkgs.writeShellScriptBin "basedpyright" ''
    echo deliberate-type-checker-failure >&2
    exit 23
  '';
  invalidReport = toolPkgs.writeShellScriptBin "basedpyright" ''
    echo invalid-json
  '';
  richMock = toolPkgs.writeShellScriptBin "basedpyright" ''
    set -eu
    [ "$#" -eq 5 ]
    [ "$1" = --project ]
    [ "$2" = "$PWD/pyproject.toml" ]
    [ "$3" = --pythonpath ]
    [ "$4" = ${python}/bin/python ]
    [ "$5" = --outputjson ]
    printf '{"generalDiagnostics":[{"file":"%s/src/app.py","severity":"error","message":"mock diagnostic","range":{"start":{"line":0,"character":0}}}],"summary":{"errorCount":1,"warningCount":0,"informationCount":0}}\n' "$PWD"
    echo checker-stderr >&2
    exit 23
  '';
  invalidPython = toolPkgs.writeTextDir "bin/python" "not executable";
  test =
    name: script:
    toolPkgs.runCommandLocal name
      {
        nativeBuildInputs = [
          toolPkgs.python3
          toolPkgs.gitMinimal
        ];
      }
      ''
        cp -r ${source} work
        chmod -R u+w work
        cd work
        touch .fixture-root
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        expect_failure() {
          if "$@" > "$TMPDIR/error" 2>&1; then
            echo "unexpected success: $*" >&2
            exit 1
          fi
          cat "$TMPDIR/error"
        }
        ${script}
        touch "$out"
      '';
  checker =
    args:
    (import ../lib/checker.nix {
      inherit lib;
      toolPkgsFor = _: toolPkgs;
    })
      ({ inherit system toolPkgs; } // args);
in
{
  basedpyright-integration = test "basedpyright-integration" ''
    # Explicit pyproject.toml must override adjacent pyrightconfig.json.
    printf '{"include":["missing"],"typeCheckingMode":"off"}' > alpha/pyrightconfig.json
    output=$(${configured.apps.lint.program})
    case "$output" in *'0 files and 2 project checks, 0 failed'*) ;; *) exit 1 ;; esac
    ${configured.apps.validate.program}
    cd alpha/src
    ${configured.apps.lint.program}
    cd ../..
    sed -i 's/"off"/"all"/' beta/pyproject.toml
    expect_failure ${configured.apps.lint.program}
    grep -F 'FAIL (basedpyright-beta): project' "$TMPDIR/error"
    ! grep -F 'FAIL (basedpyright-alpha): project' "$TMPDIR/error"
    sed -i 's/result: int/result: str/' alpha/src/app.py
    expect_failure ${configured.apps.lint.program}
    grep -F '2 project checks, 2 failed' "$TMPDIR/error"
    expect_failure ${configured.apps.validate.program}
  '';
  basedpyright-config-errors = test "basedpyright-config-errors" ''
    expect_failure ${lint { configFile = "missing/pyproject.toml"; }}
    grep -F 'missing/pyproject.toml' "$TMPDIR/error"
    printf '[project]\nname="unconfigured"\n' > alpha/pyproject.toml
    expect_failure ${configured.apps.lint.program}
    grep -F 'provide a nonempty [tool.basedpyright]' "$TMPDIR/error"
    printf '[tool.basedpyright]\n' > alpha/pyproject.toml
    expect_failure ${configured.apps.lint.program}
    printf '[invalid toml' > alpha/pyproject.toml
    expect_failure ${configured.apps.lint.program}
    cp ${source}/alpha/pyproject.toml alpha/pyproject.toml
    expect_failure ${lint { python = invalidPython; }}
    grep -F 'Python interpreter is missing or not executable' "$TMPDIR/error"
    expect_failure ${lint { package = failure; }}
    grep -F 'deliberate-type-checker-failure' "$TMPDIR/error"
    rm alpha/pyproject.toml
    ln -s ${source}/alpha/pyproject.toml alpha/pyproject.toml
    expect_failure ${configured.apps.lint.program}
    grep -F 'inside the repository' "$TMPDIR/error"
  '';
  basedpyright-rich = test "basedpyright-rich" ''
    ${rich.apps.lint.program}
    cd alpha/src
    ${rich.apps.lint.program}
    cd ../..
    sed -i 's/result: int/result: str/' alpha/src/app.py
    NO_COLOR=1 expect_failure ${rich.apps.lint.program}
    grep -F 'alpha/src/app.py:' "$TMPDIR/error"
    grep -F 'reportAssignmentType' "$TMPDIR/error"
    ! grep -F '/build/work/alpha/src/app.py' "$TMPDIR/error"
    expect_failure ${rich.apps.validate.program}
    FORCE_COLOR=1 expect_failure ${rich.apps.lint.program}
    grep -F $'\033[' "$TMPDIR/error"
    NO_COLOR=1 expect_failure ${
      lint {
        reporter = "rich";
        package = richMock;
      }
    }
    grep -F 'alpha/src/app.py:1:1: error: mock diagnostic' "$TMPDIR/error"
    grep -F 'checker-stderr' "$TMPDIR/error"
    expect_failure ${
      lint {
        reporter = "rich";
        package = invalidReport;
      }
    }
    grep -F 'invalid checker output' "$TMPDIR/error"
    expect_failure ${
      lint {
        reporter = "rich";
        package = failure;
      }
    }
    grep -F 'deliberate-type-checker-failure' "$TMPDIR/error"
  '';
  basedpyright-overrides = test "basedpyright-overrides" ''
    PYTHONPATH=/unwanted PYTHONHOME=/unwanted VIRTUAL_ENV=/unwanted ${lint { package = mock; }}
    ${lint {
      exe = lib.getExe mock;
      package = throw "exe must win";
    }}
    test "$(wc -l < "$TMPDIR/basedpyright-calls")" -eq 2
  '';
  basedpyright-coverage = test "basedpyright-coverage" ''
    git init -q
    git add .
    ${
      (project {
        lint.basedpyright.projects.alpha.exe = lib.getExe failure;
        coverage.projectChecks."lint:basedpyright-alpha" = {
          includes = [ "alpha/src/**/*.py" ];
          kind = "semantic";
          description = "Consumer-defined Python type checks";
        };
      }).apps.coverage.program
    } --json > "$TMPDIR/coverage.json"
    python - "$TMPDIR/coverage.json" <<'PY'
    import json, sys
    report = json.load(open(sys.argv[1]))
    assert report['unmapped_project_checks'] == ['lint:basedpyright-beta']
    row = next(row for row in report['files'] if row['path'] == 'alpha/src/app.py')
    assert row['project_checks'][0]['name'] == 'lint:basedpyright-alpha'
    PY
  '';
  basedpyright-sandbox-lint = configured.checks.linting;
  basedpyright-rich-sandbox-lint = rich.checks.linting;
  basedpyright-schema =
    let
      schemas = import ../lib/options.nix { inherit lib; };
      rejects = value: !(tryEval (deepSeq (checker { basedpyright = value; }).selection true)).success;
      lazy = checker {
        toolPkgs = toolPkgs // {
          basedpyright = throw "disabled tool was evaluated";
        };
      };
      metadata =
        (checker {
          basedpyright.projects.app = {
            configFile = "pyproject.toml";
            inherit python;
            outdated = {
              package = toolPkgs.basedpyright;
              provider = "pypi";
              project = "basedpyright";
            };
          };
        }).outdatedEntries;
      replaced = checker {
        basedpyright.projects.app = throw "replaced checker was evaluated";
        extraProjectCheckers.basedpyright-app.command = "true";
      };
    in
    assert
      attrNames (functionArgs schemas.basedpyrightOptions) == [
        "enable"
        "projects"
      ];
    assert
      attrNames (functionArgs schemas.basedpyrightProjectOptions) == [
        "configFile"
        "exe"
        "outdated"
        "package"
        "python"
        "reporter"
      ];
    assert rejects true;
    assert rejects { projects.app = { inherit python; }; };
    assert rejects { projects.app.configFile = "pyproject.toml"; };
    assert rejects {
      projects.app = {
        inherit python;
        configFile = "pyproject.toml";
        reporter = "unknown";
      };
    };
    assert rejects {
      projects.app = {
        configFile = "pyrightconfig.json";
        inherit python;
      };
    };
    assert rejects {
      projects.app = {
        configFile = ./fixtures/basedpyright/alpha/pyproject.toml;
        inherit python;
      };
    };
    assert (lib.head metadata).source == "lint.basedpyright-app";
    assert (lib.head replaced.selection.projectCheckers).command == "true";
    toolPkgs.runCommandLocal "basedpyright-schema" { } ''
      test -x ${lib.getExe lazy}
      touch "$out"
    '';
}
