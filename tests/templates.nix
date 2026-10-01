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
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = ./fixtures/templates;
        treeRootFile = ".fixture-root";
        exclude = [
          ".fixture-root"
          "render.py"
        ];
        format = {
          nix = false;
          shell = false;
          markdown = false;
          toml = false;
          yaml = false;
          json = false;
          justfile = false;
          whitespace = true;
        };
        lint = {
          nix = false;
          shell = false;
          yaml = false;
          workflows = false;
          salt = true;
          whitespace = true;
        };
      } args
    );
  base = project { };
  renderPython = toolPkgs.python3.withPackages (p: [
    p.jinja2
    p.pyyaml
  ]);
  test =
    name: script:
    toolPkgs.runCommandLocal name
      {
        nativeBuildInputs = [
          toolPkgs.python3
          toolPkgs.diffutils
        ];
      }
      ''
        mkdir work && cd work
        cp ${./fixtures/templates}/* .
        chmod -R u+w .
        touch .fixture-root
        export XDG_CACHE_HOME="$TMPDIR/cache"
        expect_failure() {
          if "$@" > "$TMPDIR/failure.log" 2>&1; then cat "$TMPDIR/failure.log"; exit 1; fi
          cat "$TMPDIR/failure.log"
        }
        ${script}
        touch "$out"
      '';
in
{
  templates-reject-mutable-policy =
    let
      rejects =
        phase: policy:
        !(tryEval (
          deepSeq (
            if phase == "format" then
              lib.getExe (project { format.whitespace.configFile = policy; }).formatter
            else
              (project { lint.whitespace.configFile = policy; }).apps.lint.program
          ) true
        )).success;
    in
    assert lib.all
      (
        phase:
        lib.all (rejects phase) [
          "policy"
          "/tmp/editorconfig"
        ]
      )
      [
        "format"
        "lint"
      ];
    test "templates-reject-mutable-policy" ''
      # A derivation output is immutable and accepted alongside literal Nix paths.
      ${lib.getExe
        (project {
          format.whitespace.configFile = toolPkgs.writeText "editorconfig" ''
            root = true
            [*]
            end_of_line = crlf
            insert_final_newline = true
            trim_trailing_whitespace = true
          '';
        }).formatter
      }
      ${lib.getExe base.formatter}
      ${lib.getExe base.formatter}
      ${base.apps.lint.program}
    '';
  templates-reject-native-exclusions = test "templates-reject-native-exclusions" ''
    printf 'BAD  \n' > z.j2
    cp z.j2 "$TMPDIR/original.j2"
    ${lib.concatMapStringsSep "\n"
      (options: ''
        expect_failure ${
          (project {
            lint.salt = false;
            lint.whitespace.extraOptions = options;
          }).apps.lint.program
        }
        grep -q 'use the exclude option instead of native --exclude flags' "$TMPDIR/failure.log"
        cmp z.j2 "$TMPDIR/original.j2"
        expect_failure ${
          lib.getExe
            (project {
              format.whitespace.extraOptions = options;
            }).formatter
        }
        grep -q 'use the exclude option instead of native --exclude flags' "$TMPDIR/failure.log"
        cmp z.j2 "$TMPDIR/original.j2"
      '')
      [
        [
          "--exclude"
          "content"
        ]
        [
          "-exclude"
          "content"
        ]
        [ "--exclude=content" ]
        [ "-exclude=content" ]
      ]
    }
    # Supported exclusions match original paths, before temporary copies exist.
    ${(project {
      lint.salt = false;
      lint.whitespace.exclude = [ "z.j2" ];
    }).apps.lint.program
    }
    ${lib.getExe
      (project {
        format.whitespace.exclude = [ "z.j2" ];
        format.exclude = [ "z.j2" ];
      }).formatter
    }
    cmp z.j2 "$TMPDIR/original.j2"
    expect_failure ${base.apps.lint.program}
  '';
  templates-reject-lint-fix = test "templates-reject-lint-fix" ''
    printf 'BAD  \n' > z.j2
    cp z.j2 "$TMPDIR/original.j2"
    ${lib.concatMapStringsSep "\n"
      (flag: ''
        expect_failure ${
          (project {
            lint.salt = false;
            lint.whitespace.extraOptions = [ flag ];
          }).apps.lint.program
        }
        grep -q 'fixing flags are not allowed in check mode' "$TMPDIR/failure.log"
        cmp z.j2 "$TMPDIR/original.j2"
      '')
      [
        "--fix"
        "-fix"
        "--fix=true"
        "-fix=true"
        "--fix=false"
        "-fix=false"
      ]
    }
    # Ordinary lint still finds the violation; the formatter is the way to fix it.
    expect_failure ${base.apps.lint.program}
    ${lib.getExe base.formatter}
    ${base.apps.lint.program}
  '';
  templates-behavior = test "templates-behavior" ''
    ${base.apps.lint.program}
    cp -r . "$TMPDIR/expected"
    python - <<'PY'
    from pathlib import Path
    for path in Path('.').iterdir():
        if path.suffix in ('.sls', '.j2', '.jinja'):
            path.write_bytes(path.read_bytes().replace(b'\n', b'  \r\n'))
    PY
    expect_failure ${base.apps.lint.program}
    ${lib.getExe base.formatter}
    diff -r --exclude=.tmp . "$TMPDIR/expected"
    ${lib.getExe base.formatter} --ci --no-cache
    ${(project {
      lint.extraProjectCheckers.render = {
        command = "${renderPython}/bin/python";
        options = [ "render.py" ];
      };
    }).apps.lint.program
    }
    printf '{{bad}}\n' > z.j2
    expect_failure ${base.apps.lint.program}
    grep -q 'salt' "$TMPDIR/failure.log"
    grep -q z.j2 "$TMPDIR/failure.log"
    rm z.j2
    printf '{{ missing }}\n' >> data.j2
    expect_failure ${
      (project {
        lint.extraProjectCheckers.render = {
          command = "${renderPython}/bin/python";
          options = [ "render.py" ];
        };
      }).apps.lint.program
    }
    grep -q UndefinedError "$TMPDIR/failure.log"
  '';
  templates-whitespace-policy = test "templates-whitespace-policy" ''
    mkdir nested
    cp data.j2 nested/data.j2
    cp service.jinja "$TMPDIR/service.original"
    expect_failure ${
      lib.getExe
        (project {
          format.whitespace.configFile = ./conf/editorconfig-sections;
        }).formatter
    }
    cmp service.jinja "$TMPDIR/service.original"
    # Properties match the original relative path, including directory sections.
    python - <<'PYTEST'
    from pathlib import Path
    assert b'\r\n' in Path('nested/data.j2').read_bytes()
    assert b'\r' not in Path('data.j2').read_bytes()
    PYTEST
    chmod +x state.sls
    printf '  ' >> state.sls
    ${lib.getExe base.formatter}
    test -x state.sls
  '';
  templates-overrides =
    let
      native = toolPkgs.writeShellScriptBin "editorconfig-checker" ''
        printf '%s\n' "$@" >> "$TMPDIR/native-args"
      '';
      salt = toolPkgs.writeShellScriptBin "salt-lint" ''
        printf '%s\n' "$@" >> "$TMPDIR/salt-args"
      '';
      configured = project {
        format.whitespace = {
          package = native;
          includes = [ "*.j2" ];
          extraOptions = [ "--disable-indentation" ];
          priority = 2;
        };
        format.exclude = [
          "*.sls"
          "*.jinja"
        ];
        lint.whitespace = {
          exe = lib.getExe native;
          includes = [ "*.j2" ];
        };
        lint.salt = {
          package = salt;
          batch = false;
          includes = [ "*.j2" ];
        };
      };
    in
    test "templates-overrides" ''
      ${lib.getExe base.formatter}
      ${lib.getExe configured.formatter}
      grep -q -- '--fix' "$TMPDIR/native-args"
      grep -q -- '--disable-indentation' "$TMPDIR/native-args"
      ${configured.apps.lint.program}
      grep -q -- '--nocolor' "$TMPDIR/salt-args"
      grep -q data.j2 "$TMPDIR/salt-args"
      ! grep -q state.sls "$TMPDIR/salt-args"
      test "$(grep -c -- '--config' "$TMPDIR/native-args")" = 2
      ${lib.getExe configured.formatter}
      test "$(grep -c -- '--config' "$TMPDIR/native-args")" = 2
    '';
  templates-configuration-cache = test "templates-configuration-cache" ''
    ${lib.getExe base.formatter}
    # Deliberately hostile ambient policies cannot change the explicit policy.
    printf 'root = true\n[*]\nend_of_line = crlf\n' > .editorconfig
    printf 'skip_list: [206]\n' > .salt-lint
    ${lib.getExe (project { format.exclude = [ ".salt-lint" ]; }).formatter} --ci --no-cache
    ${(project { lint.exclude = [ ".salt-lint" ]; }).apps.lint.program}
    ${lib.getExe
      (project {
        format.exclude = [ ".salt-lint" ];
        format.whitespace.configFile = ./conf/editorconfig;
      }).formatter
    }
    python - <<'PY'
    from pathlib import Path
    for name in ('state.sls', 'data.j2', 'service.jinja'):
        data = Path(name).read_bytes()
        assert b'\r\n' in data and not data.endswith(b'\n'), repr(data)
    PY
    ${(project { lint.whitespace.configFile = ./conf/editorconfig; }).apps.lint.program}
    expect_failure ${base.apps.lint.program}
    ${lib.getExe (project { format.exclude = [ ".salt-lint" ]; }).formatter}
    # The config file itself is a valid explicit whitespace target.
    ${lib.getExe
      (project {
        unexclude = [ ".editorconfig" ];
        format.exclude = [ ".salt-lint" ];
        format.whitespace.includes = [
          "*.sls"
          "*.j2"
          "*.jinja"
          ".editorconfig"
        ];
      }).formatter
    }
    mkdir nested
    printf 'end_of_line = crlf\n' > nested/.editorconfig
    cp state.sls nested/child.sls
    ${lib.getExe (project { format.exclude = [ ".salt-lint" ]; }).formatter} --ci --no-cache
    printf '{{bad}}\n' > z.j2
    expect_failure ${base.apps.lint.program}
    ${(project {
      lint.salt.extraOptions = [
        "-x"
        "206"
      ];
    }).apps.lint.program
    }
  '';
}
