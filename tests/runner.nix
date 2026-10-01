{
  api,
  lib,
  system,
  toolPkgs,
}:
let
  inherit (builtins) deepSeq tryEval;
  project =
    args:
    api.configure (
      {
        inherit system;
        src = ./fixtures/chk/pass-php;
        treeRootFile = ".fixture-root";
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
        }
        // (args.lint or { });
      }
      // removeAttrs args [ "lint" ]
    );
  reader = toolPkgs.writeText "stdin-reader.py" ''
    import pathlib, sys
    content = sys.stdin.read()
    if "--inherit" in sys.argv:
        assert content == "caller input\n", repr(content)
        sys.argv.remove("--inherit")
    else:
        assert content == "", repr(content)
    with open("visited", "a") as log:
        log.write("\n".join(sys.argv[1:]) + "\n")
    sys.exit(any("BAD" in pathlib.Path(p).read_text() for p in sys.argv[1:]))
  '';
  spec = {
    command = lib.getExe toolPkgs.python3;
    options = [ reader ];
    includes = [ "*.probe" ];
  };
  badFd =
    status:
    toolPkgs.writeShellScriptBin "fd" ''
      printf './a.probe\0'
      echo 'intentional discovery failure' >&2
      exit ${toString status}
    '';
  test =
    name: script:
    toolPkgs.runCommandLocal name { } ''
      mkdir work && cd work
      touch .fixture-root
      printf GOOD > a.probe
      printf BAD > z.probe
      ${script}
      touch "$out"
    '';
  byMode =
    batch:
    let
      label = if batch then "batch" else "per-file";
      checker =
        (project {
          lint.extraCheckers.probe = spec // {
            inherit batch;
          };
        }).apps.lint.program;
      broken =
        status:
        (project {
          toolPkgs = toolPkgs // {
            fd = badFd status;
          };
          lint.extraCheckers.probe = spec // {
            inherit batch;
          };
        }).apps.lint.program;
    in
    lib.listToAttrs (
      map
        (
          multiple:
          let
            variant = if multiple then "multiple" else "single";
            count = if multiple then "2" else "1";
            selected =
              (project {
                lint.extraCheckers.probe = spec // {
                  inherit batch;
                  searchPaths = lib.optional multiple "visible" ++ [ "locked/nested" ];
                };
              }).apps.lint.program;
          in
          {
            name = "runner-root-permissions-${variant}-${label}";
            value = test "runner-root-permissions-${variant}-${label}" ''
              mkdir -p visible locked/nested
              mv a.probe visible/
              mv z.probe locked/nested/
              trap 'chmod 700 locked' EXIT
              chmod 000 locked
              if ${selected} > log 2>&1; then cat log; exit 1; fi
              cat log
              grep -q 'FAIL (probe): discovery' log
              grep -q 'Permission denied' log
              if [ -e visited ]; then ! grep -q z.probe visited; fi
              chmod 700 locked
              if ${selected} > log 2>&1; then cat log; exit 1; fi
              grep -q 'checked ${count} files' log
              grep -q z.probe visited
              rm locked/nested/z.probe
              rmdir locked/nested
              ${selected} > log 2>&1
            '';
          }
        )
        [
          false
          true
        ]
    )
    // {
      "runner-globs-${label}" = test "runner-globs-${label}" ''
        printf BAD > 'part,one.probe'
        printf GOOD > '-dash.probe'
        printf GOOD > 'literal{brace}.probe'
        printf GOOD > 'literal[bracket].probe'
        if ${
          (project {
            lint.extraCheckers.probe = spec // {
              inherit batch;
              includes = [ "part,one.probe" ];
            };
          }).apps.lint.program
        } > log 2>&1; then cat log; exit 1; fi
        grep -q 'checked 1 files' log
        grep -q 'part,one.probe' visited
        rm visited
        if ${
          (project {
            lint.extraCheckers.probe = spec // {
              inherit batch;
              includes = [
                "part,one.probe"
                "{a,z}.probe"
                "a.probe"
                "-dash.probe"
                "literal\\{brace\\}.probe"
                "literal\\[bracket\\].probe"
              ];
            };
          }).apps.lint.program
        } > log 2>&1; then cat log; exit 1; fi
        cat log
        grep -q 'checked 6 files' log
        test "$(wc -l < visited)" -eq 6
        grep -q -- '-dash.probe' visited
        grep -Fq 'literal{brace}.probe' visited
        grep -Fq 'literal[bracket].probe' visited
      '';
      "runner-discovery-later-pattern-${label}" = test "runner-discovery-later-pattern-${label}" ''
        if ${
          (project {
            lint.extraCheckers.probe = spec // {
              inherit batch;
              includes = [
                "a.probe"
                "[unterminated"
              ];
            };
          }).apps.lint.program
        } > log 2>&1; then cat log; exit 1; fi
        cat log
        grep -q 'discovery' log
        grep -q 'checked 0 files' log
        test ! -e visited
      '';
      "runner-stdin-${label}" = test "runner-stdin-${label}" ''
        if printf 'caller input\n' | ${checker} > log 2>&1; then cat log; exit 1; fi
        cat log
        grep -q 'checked 2 files' log
        grep -q a.probe visited
        grep -q z.probe visited
      '';
      "runner-discovery-${label}" = test "runner-discovery-${label}" ''
        for checker in ${broken 0} ${broken 47}; do
          if "$checker" > log 2>&1; then cat log; exit 1; fi
          cat log
          grep -q 'intentional discovery failure' log
          grep -q 'discovery' log
          grep -q 'checked 0 files' log
          test ! -e visited
        done
      '';
      "runner-unreadable-directory-${label}" = test "runner-unreadable-directory-${label}" ''
        mkdir locked
        mv z.probe locked/
        trap 'chmod 700 locked' EXIT
        chmod 000 locked
        if ${checker} > log 2>&1; then cat log; exit 1; fi
        cat log
        grep -q 'Permission denied' log
        grep -q 'FAIL (probe): discovery' log
        grep -q 'checked 0 files' log
        test ! -e visited
        ${
          (project {
            lint.extraCheckers.probe = spec // {
              inherit batch;
              exclude = [ "locked" ];
            };
          }).apps.lint.program
        } > log 2>&1
        grep -q 'checked 1 files' log
        rm visited
        chmod 700 locked
        if ${checker} > log 2>&1; then cat log; exit 1; fi
        grep -q 'checked 2 files' log
        grep -q a.probe visited
        grep -q z.probe visited
      '';
    };
  rejects =
    includes:
    !(tryEval (
      deepSeq
        (project {
          lint.extraCheckers.probe = spec // {
            inherit includes;
          };
        }).apps.lint.program
        true
    )).success;
in
byMode true
// byMode false
// {
  runner-invalid-search-root = test "runner-invalid-search-root" ''
    check=${
      (project {
        lint.extraCheckers.probe = spec // {
          searchPaths = [ "selected" ];
        };
      }).apps.lint.program
    }
    printf BAD > selected
    if "$check" > log 2>&1; then cat log; exit 1; fi
    grep -q 'not a discovery directory' log
    rm selected
    ln -s selected selected
    if "$check" > log 2>&1; then cat log; exit 1; fi
    grep -q 'Too many levels of symbolic links' log
    test ! -e visited
    rm selected
    "$check" > log 2>&1
    grep -q 'checked 0 files' log
  '';
  runner-single-search-root = test "runner-single-search-root" ''
    mkdir 'nested path'
    mv a.probe z.probe 'nested path/'
    if ${
      (project {
        lint.extraCheckers.probe = spec // {
          searchPaths = [ "nested path" ];
        };
      }).apps.lint.program
    } > log 2>&1; then cat log; exit 1; fi
    grep -q 'checked 2 files' log
    grep -q z.probe visited
    ${
      (project {
        lint.extraCheckers.probe = spec // {
          searchPaths = [ "missing" ];
        };
      }).apps.lint.program
    } > log 2>&1
    grep -q 'checked 0 files' log
    ${
      (project {
        lint.extraCheckers.probe = spec // {
          searchPaths = [ ];
        };
      }).apps.lint.program
    } > log 2>&1
    grep -q 'checked 0 files' log
    ${
      (project {
        lint.extraCheckers.probe = spec // {
          includes = [ ];
        };
      }).apps.lint.program
    } > log 2>&1
    grep -q 'checked 0 files' log
  '';
  runner-path-glob =
    assert rejects [ "nested/broken.py" ];
    assert rejects [ "**/*.py" ];
    toolPkgs.runCommandLocal "runner-path-glob" { } ''touch "$out"'';
  runner-stdin-inherit = test "runner-stdin-inherit" ''
    rm z.probe
    printf 'caller input\n' | ${
      (project {
        lint.extraCheckers.probe = spec // {
          stdin = "inherit";
          options = [
            reader
            "--inherit"
          ];
        };
      }).apps.lint.program
    }
    grep -q a.probe visited
  '';
}
