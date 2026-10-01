{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins) deepSeq tryEval;
  source = ./fixtures/coverage;
  project =
    args:
    api.configure (
      let
        base = {
          inherit system toolPkgs;
          src = source;
          treeRootFile = ".fixture-root";
          exclude = [
            ".fixture-root"
            "LICENSE"
          ];
          format = {
            python = true;
            shell.includes = [ "bin/check" ];
            whitespace = true;
          };
          lint = {
            python = true;
            shell.includes = [ "check" ];
            whitespace = true;
            extraProjectCheckers.templates.command = "false";
          };
          coverage = {
            projectChecks."lint:templates" = {
              includes = [ "*.jinja" ];
              kind = "semantic";
              description = "Strictly render templates and validate their output";
            };
            exceptions = [
              {
                includes = [ "LICENSE" ];
                reason = "Verbatim upstream license";
              }
              {
                includes = [ "*.lock" ];
                reason = "Generated dependency locks; validated by their producer";
              }
              {
                includes = [
                  ".gitignore"
                  ".fixture-root"
                ];
                reason = "Repository metadata";
              }
            ];
          };
        };
      in
      lib.recursiveUpdate base (
        removeAttrs args [
          "clearProjects"
          "clearDeclarations"
        ]
      )
      // lib.optionalAttrs (args.clearProjects or false) {
        lint = base.lint // {
          extraProjectCheckers = { };
        };
        coverage = base.coverage // {
          projectChecks = { };
        };
      }
      // lib.optionalAttrs (args.clearDeclarations or false) {
        coverage = base.coverage // {
          projectChecks = { };
        };
      }
    );
  report = args: (project args).apps.coverage.program;
  checkReason =
    !(tryEval (
      deepSeq (report {
        coverage.exceptions = [
          {
            includes = [ "*" ];
            reason = "";
          }
        ];
      }) true
    )).success;
  checkReference =
    !(tryEval (
      deepSeq (report {
        coverage.projectChecks."lint:missing" = {
          includes = [ "*" ];
          kind = "test";
          description = "missing";
        };
      }) true
    )).success;
  badFd =
    status:
    toolPkgs.writeShellScriptBin "fd" ''
      printf './nested/tool.py\0'
      echo 'coverage discovery failure' >&2
      exit ${toString status}
    '';
  test =
    name: script:
    toolPkgs.runCommandLocal name
      {
        nativeBuildInputs = [
          toolPkgs.gitMinimal
          toolPkgs.python3
          toolPkgs.nix
        ];
      }
      ''
        mkdir work && cd work
        cp -r ${source}/. .
        chmod -R u+w .
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        git init -q
        git add .
        ${script}
        touch "$out"
      '';
in
{
  coverage-schema =
    assert checkReason;
    assert checkReference;
    toolPkgs.runCommandLocal "coverage-schema" { } ''touch "$out"'';
  coverage-behavior = test "coverage-behavior" ''
    mkdir build
    printf ignored > build/artifact.py
    printf untracked > missing.py
    ${report { }} --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PY'
    import json, sys
    data = json.load(open(sys.argv[1]))
    rows = {row['path']: row for row in data['files']}
    assert {x['name'] for x in rows['nested/tool.py']['format']} == {'python'}
    assert {x['name'] for x in rows['nested/tool.py']['lint']} == {'python'}
    assert {x['name'] for x in rows['bin/check']['format']} == {'shell'}
    assert {x['name'] for x in rows['bin/check']['lint']} == {'shell'}
    assert rows['future.newext']['gaps'] == ['format', 'lint']
    assert rows['dependencies.lock']['format'] == []
    assert rows['dependencies.lock']['exceptions'][0]['reason'].startswith('Generated')
    assert rows['LICENSE']['exceptions'][0]['reason'] == 'Verbatim upstream license'
    assert rows['LICENSE']['gaps'] == []
    assert rows['view.jinja']['format'] == [{'name': 'whitespace', 'kind': 'whitespace'}]
    assert rows['view.jinja']['lint'] == [{'name': 'whitespace', 'kind': 'whitespace'}]
    assert rows['view.jinja']['project_checks'][0]['scope'] == 'declared'
    assert rows['view.jinja']['gaps'] == []
    assert data['untracked'] == [{'path': 'missing.py', 'reason': 'Untracked files are omitted by Git flakes; add intended sources to Git.', 'in_flake_source': False}]
    assert data['ignored'][0]['path'] == 'build/artifact.py'
    assert data['unmapped_project_checks'] == []
    PY
    if ${report { }} --check > "$TMPDIR/check.log"; then exit 1; fi
    # No formatter, checker or false project command was run by the audit.
    test "$(cat future.newext)" = 'not understood'
    rm missing.py
    ${
      report {
        coverage.exceptions = [
          {
            includes = [ "*" ];
            reason = "Audit policy test";
          }
        ];
      }
    } --check
    ${report { clearDeclarations = true; }} --json > "$TMPDIR/unmapped.json"
    python - "$TMPDIR/unmapped.json" <<'PYTEST'
    import json, sys
    data = json.load(open(sys.argv[1]))
    assert data['unmapped_project_checks'] == ['lint:templates']
    PYTEST
  '';
  coverage-whitespace-is-not-semantic = test "coverage-whitespace-is-not-semantic" ''
    ${
      report {
        clearProjects = true;
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PY'
    import json, sys
    data = json.load(open(sys.argv[1]))
    row = next(row for row in data['files'] if row['path'] == 'view.jinja')
    assert row['gaps'] == ['lint'], row
    PY
  '';
  coverage-git-flake-omission = test "coverage-git-flake-omission" ''
    printf 'new source' > untracked.py
    export XDG_CACHE_HOME="$TMPDIR/cache"
    export NIX_CONFIG='experimental-features = nix-command flakes'
    nix --store "$TMPDIR/store" eval --no-write-lock-file --json .#files > "$TMPDIR/git-files.json"
    nix --store "$TMPDIR/store" eval --no-write-lock-file --json path:.#files > "$TMPDIR/path-files.json"
    ${report { }} --json > "$TMPDIR/report.json"
    python - "$TMPDIR" <<'PYTEST'
    import json, pathlib, sys
    root = pathlib.Path(sys.argv[1])
    load = lambda name: json.loads((root / name).read_text())
    assert 'untracked.py' not in load('git-files.json')
    assert 'untracked.py' in load('path-files.json')
    assert load('report.json')['untracked'][0]['path'] == 'untracked.py'
    assert not load('report.json')['untracked'][0]['in_flake_source']
    PYTEST
  '';
  coverage-project-tests = test "coverage-project-tests" ''
    ${
      report {
        validate.steps = [
          {
            name = "unit";
            run = "false";
          }
        ];
        coverage.required = [
          "format"
          "lint"
          "tests"
        ];
        coverage.projectChecks."validate:unit" = {
          includes = [ "*.py" ];
          exclude = [ "ignored.py" ];
          kind = "test";
          description = "Unit tests for Python source";
        };
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    rows = {row['path']: row for row in json.load(open(sys.argv[1]))['files']}
    assert rows['nested/tool.py']['gaps'] == []
    assert rows['nested/tool.py']['project_checks'][0]['kind'] == 'test'
    assert rows['bin/check']['gaps'] == ['tests']
    PYTEST
  '';
  coverage-missing-and-excluded = test "coverage-missing-and-excluded" ''
    printf added > added.py
    git add added.py
    rm nested/tool.py
    ${
      report {
        format.shell.exclude = [ "bin/check" ];
        lint.shell.exclude = [ "bin/check" ];
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    rows = {row['path']: row for row in json.load(open(sys.argv[1]))['files']}
    assert 'missing-file' in rows['nested/tool.py']['gaps']
    assert 'flake-source' in rows['added.py']['gaps']
    assert rows['bin/check']['format'] == []
    assert rows['bin/check']['lint'] == []
    PYTEST
  '';
  coverage-custom-kinds = test "coverage-custom-kinds" ''
    ${
      report {
        format.extraFormatters.future = {
          command = toolPkgs.writeShellScript "never-run" "exit 99";
          includes = [ "*.newext" ];
          cacheInputs = [ ];
        };
        lint.extraCheckers.future = {
          command = "false";
          includes = [ "*.newext" ];
        };
        coverage.checkerKinds.future = "syntax";
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'future.newext')
    assert row['format'] == [{'name': 'future', 'kind': 'format'}]
    assert row['lint'] == [{'name': 'future', 'kind': 'syntax'}]
    assert row['gaps'] == []
    PYTEST
  '';
  coverage-normalized-search-roots = test "coverage-normalized-search-roots" ''
    printf GOOD > 'nested/part,one.probe'
    git add 'nested/part,one.probe'
    ${
      report {
        lint.extraCheckers.probe = {
          command = "false";
          includes = [
            "part,one.probe"
            "*.probe"
          ];
          searchPaths = [
            "nested/."
            "absent"
          ];
        };
        coverage.checkerKinds.probe = "semantic";
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'nested/part,one.probe')
    assert row['lint'] == [{'name': 'probe', 'kind': 'semantic'}], row
    PYTEST
    ${
      report {
        lint.extraCheckers.probe = {
          command = "false";
          includes = [ "*.py" ];
          searchPaths = [ "nested/../nested" ];
        };
        coverage.checkerKinds.probe = "semantic";
      }
    } --json > "$TMPDIR/parent.json"
    python - "$TMPDIR/parent.json" <<'PYTEST'
    import json, sys
    row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'nested/tool.py')
    assert {'name': 'probe', 'kind': 'semantic'} in row['lint'], row
    PYTEST
    ${
      report {
        toolPkgs = toolPkgs // {
          fd = toolPkgs.writeShellScriptBin "fd" ''
            exec ${lib.getExe toolPkgs.fd} --absolute-path "$@"
          '';
        };
      }
    } --json > "$TMPDIR/absolute.json"
    python - "$TMPDIR/absolute.json" <<'PYTEST'
    import json, sys
    row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'nested/tool.py')
    assert row['lint'] == [{'name': 'python', 'kind': 'semantic'}], row
    PYTEST
  '';
  coverage-debian-selection = test "coverage-debian-selection" ''
    cp -r ${./fixtures/chk/pass-debian}/debian .
    chmod -R u+w debian
    git add debian
    ${
      report {
        lint.debian = true;
        format.debian = true;
        lint.exclude = [ "debian/changelog" ];
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    data = json.load(open(sys.argv[1]))
    rows = {row['path']: row for row in data['files']}
    assert rows['debian/control']['lint'] == [{'name': 'debian', 'kind': 'semantic'}]
    assert rows['debian/control']['format'] == [{'name': 'debian', 'kind': 'format'}]
    assert rows['debian/changelog']['lint'] == []
    assert 'lint:debian' not in data['unmapped_project_checks']
    PYTEST
  '';
  coverage-no-formatters = test "coverage-no-formatters" ''
    ${
      report {
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          python = false;
          whitespace = false;
        };
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    assert all(not row['format'] for row in json.load(open(sys.argv[1]))['files'])
    PYTEST
  '';
  coverage-discovery-failure = test "coverage-discovery-failure" ''
    for reporter in ${
      lib.concatMapStringsSep " "
        (
          status:
          report {
            toolPkgs = toolPkgs // {
              fd = badFd status;
            };
          }
        )
        [
          0
          47
        ]
    }; do
      if "$reporter" --json > "$TMPDIR/report.json" 2> "$TMPDIR/error"; then exit 1; fi
      grep -q 'coverage discovery failure' "$TMPDIR/error"
      test ! -s "$TMPDIR/report.json"
    done
  '';
  coverage-search-root-errors =
    let
      selected = report {
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          python = false;
          whitespace = false;
        };
        lint = {
          nix = false;
          shell = false;
          python = false;
          yaml = false;
          workflows = false;
          whitespace = false;
          extraCheckers.probe = {
            command = "false";
            includes = [ "*.probe" ];
            searchPaths = [ "locked/nested" ];
          };
        };
      };
    in
    test "coverage-search-root-errors" ''
      mkdir -p locked/nested
      printf BAD > locked/nested/z.probe
      git add locked
      trap 'chmod 700 locked' EXIT
      chmod 000 locked
      if ${selected} --json > "$TMPDIR/report.json" 2> "$TMPDIR/error"; then exit 1; fi
      grep -q 'Permission denied' "$TMPDIR/error"
      test ! -s "$TMPDIR/report.json"
      chmod 700 locked
      ${selected} --json > "$TMPDIR/report.json"
      python - "$TMPDIR/report.json" <<'PYTEST'
      import json, sys
      row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'locked/nested/z.probe')
      assert row['lint'] == [{'name': 'probe', 'kind': 'unspecified'}], row
      PYTEST
      rm locked/nested/z.probe
      rmdir locked/nested
      ${selected} --json > "$TMPDIR/report.json"
      printf BAD > locked/nested
      if ${selected} --json > "$TMPDIR/report.json" 2> "$TMPDIR/error"; then exit 1; fi
      grep -q 'not a discovery directory' "$TMPDIR/error"
      test ! -s "$TMPDIR/report.json"
      rm locked/nested
      ln -s nested locked/nested
      if ${selected} --json > "$TMPDIR/report.json" 2> "$TMPDIR/error"; then exit 1; fi
      grep -q 'Too many levels of symbolic links' "$TMPDIR/error"
      test ! -s "$TMPDIR/report.json"
    '';
  coverage-unreadable-directory = test "coverage-unreadable-directory" ''
    mkdir locked
    printf 'broken Python {' > locked/bad.py
    # Keep this directory outside treefmt's Git walk to exercise fd's errors.
    trap 'chmod 700 locked' EXIT
    chmod 000 locked
    if ${
      report { format.exclude = [ "locked/**" ]; }
    } --json > "$TMPDIR/report.json" 2> "$TMPDIR/error"; then exit 1; fi
    cat "$TMPDIR/error"
    grep -q 'file discovery failed' "$TMPDIR/error"
    grep -q 'Permission denied' "$TMPDIR/error"
    test ! -s "$TMPDIR/report.json"
    ${
      report {
        format.exclude = [ "locked/**" ];
        lint.exclude = [ "locked" ];
      }
    } --json > "$TMPDIR/report.json"
    python - "$TMPDIR/report.json" <<'PYTEST'
    import json, sys
    row = next(row for row in json.load(open(sys.argv[1]))['files'] if row['path'] == 'nested/tool.py')
    assert row['lint'] == [{'name': 'python', 'kind': 'semantic'}], row
    PYTEST
  '';
}
