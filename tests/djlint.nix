{
  api,
  lib,
  system,
  toolPkgs,
  renderPython,
}:
let
  inherit (builtins) deepSeq toJSON tryEval;
  djlint = import ../lib/djlint.nix { inherit lib toolPkgs; };
  inherit (djlint) profiles;
  project =
    args:
    api.configure (
      lib.recursiveUpdate {
        inherit system toolPkgs;
        src = ./fixtures/djlint;
        treeRootFile = ".fixture-root";
        exclude = [ ".fixture-root" ];
        format = lib.genAttrs [
          "nix"
          "shell"
          "markdown"
          "toml"
          "yaml"
          "json"
          "justfile"
        ] (_: false);
        lint = lib.genAttrs [ "nix" "shell" "yaml" "workflows" ] (_: false);
      } args
    );
  programs =
    args:
    let
      p = project args;
    in
    {
      format = lib.getExe p.formatter;
      lint = p.apps.lint.program;
      coverage = p.apps.coverage.program;
    };
  shared = {
    includes = [ "*.html.njk" ];
  };
  base = {
    format.nunjucks = shared;
    lint.nunjucks = shared;
  };
  configured = args: programs (lib.recursiveUpdate base args);
  policy = name: contents: toolPkgs.writeText name contents;
  indent2 = policy "djlint.toml" "indent = 2\n";
  spy = toolPkgs.writeShellScriptBin "djlint" ''
    printf '%s\n' "$@" >> "$NIX_TOOLS_DJLINT_LOG"
    exec ${lib.getExe toolPkgs.djlint} "$@"
  '';

  # Published compiler/runtime bundle, used only by the tests.
  # No consumer Node dependency.
  nunjucks = toolPkgs.runCommandLocal "nunjucks-test-runtime" { } ''
    mkdir -p "$out"
    tar -xzf ${
      toolPkgs.fetchurl {
        url = "https://registry.npmjs.org/nunjucks/-/nunjucks-3.2.4.tgz";
        hash = "sha256-PAYMe9W+NsOisM3wHWWJJTpe+zxm5eBiV92LCKzC8W8=";
      }
    } --strip-components=2 -C "$out" package/browser/nunjucks.js
  '';
  settings = {
    inherit profiles;
    node = lib.getExe toolPkgs.nodejs_24;
    nunjucks = "${nunjucks}/nunjucks.js";
    renderScript = ./fixtures/djlint/render.cjs;
    variants = {
      base = configured { };
      perFile = configured { lint.nunjucks.batch = false; };
      delimiter = configured {
        exclude = [
          ".fixture-root"
          "--"
          "outside.txt"
        ];
        format.nunjucks.extraOptions = [
          "--"
          "outside.txt"
        ];
        lint.nunjucks.extraOptions = [
          "--"
          "outside.txt"
        ];
      };
      delimiterPerFile = configured {
        exclude = [
          ".fixture-root"
          "--"
          "outside.txt"
        ];
        lint.nunjucks.extraOptions = [
          "--"
          "outside.txt"
        ];
        lint.nunjucks.batch = false;
      };
      delimiterFile = configured {
        format.nunjucks.includes = [ "--" ];
        lint.nunjucks.includes = [ "--" ];
      };
      unsafeExpressions = configured {
        format.nunjucks.configFile = policy "djlint.toml" ''
          no_set_formatting = false
          no_function_formatting = false
        '';
      };
      indent2 = configured { format.nunjucks.configFile = indent2; };
      json = configured { format.nunjucks.configFile = policy ".djlintrc" ''{"indent":2}''; };
      pyproject = configured {
        format.nunjucks.configFile = policy "pyproject.toml" "[tool.djlint]\nindent = 2\n";
      };
      flatSuffix = configured {
        format.nunjucks.configFile = policy "custom-pyproject.toml" "indent = 2\n";
      };
      contextual = configured {
        format.nunjucks.configFile = "${policy "pyproject.toml" "[tool.djlint]\nindent = 2\n"}";
      };
      subpath = configured {
        format.nunjucks.configFile = "${
          toolPkgs.runCommandLocal "nested-policy" { } ''
            mkdir "$out"
            printf '[tool.djlint]\nindent = 2\n' > "$out/pyproject.toml"
          ''
        }/pyproject.toml";
      };
      extra = configured {
        format.nunjucks.extraOptions = [
          "--indent"
          "2"
        ];
      };
      ignore = configured {
        lint.nunjucks.configFile = policy ".djlintrc" ''{"per-file-ignores":{"nested/z.html.njk":"H013"}}'';
      };
      exclude = configured {
        exclude = [
          ".fixture-root"
          "skip.html.njk"
        ];
        format.exclude = [ "format.html.njk" ];
        lint.exclude = [ "lint.html.njk" ];
        format.nunjucks.exclude = [ "local.html.njk" ];
        lint.nunjucks.exclude = [ "local.html.njk" ];
      };
      independent = configured {
        format.jinja = {
          includes = [ "*.html.j2" ];
          configFile = indent2;
        };
        lint.jinja = false;
        format.django = {
          includes = [ "*.html.dtl" ];
          configFile = indent2;
        };
        lint.django = {
          includes = [ "*.html.dtl" ];
          configFile = indent2;
        };
      };
      extensionless = programs {
        format.nunjucks.includes = [ "nested/template" ];
        lint.nunjucks.includes = [ "template" ];
      };
      overrides = configured {
        format.nunjucks.package = spy;
        lint.nunjucks.exe = lib.getExe spy;
        format.nunjucks.configFile = policy "djlint.toml" ''profile = "django"'';
        lint.nunjucks.configFile = policy "djlint.toml" ''profile = "django"'';
      };
      failedTool = configured {
        format.nunjucks.exe = toolPkgs.writeShellScript "djlint-failed" ''
          printf '<p>Partial output</p>\n'
          exit 1
        '';
      };
      emptyTool = configured {
        format.nunjucks.exe = toolPkgs.writeShellScript "djlint-empty" "exit 0";
      };
    };
    languages = lib.genAttrs profiles (
      profile:
      programs {
        format.${profile}.includes = [ "*.template" ];
        lint.${profile}.includes = [ "*.template" ];
      }
    );
    badPolicies =
      map
        (
          text:
          configured {
            format.nunjucks.configFile = policy ".djlintrc" text;
            lint.nunjucks.configFile = policy ".djlintrc" text;
          }
        )
        [
          "{"
          "[]"
          ''{"indent":"bad"}''
          ''{"format_js":"false"}''
          ''{"files":["/tmp/elsewhere"]}''
          ''{"exclude":"*"}''
          ''{"extend_exclude":"*"}''
          ''{"require_pragma":true}''
          ''{"use_gitignore":true}''
          ''{"unknown_option":true}''
        ];
    badOptions =
      map
        (
          flag:
          configured {
            format.nunjucks.extraOptions = [ flag ];
            lint.nunjucks.extraOptions = [ flag ];
          }
        )
        [
          "--warn"
          "--reformat"
          "--check"
          "--lint"
          "--require-pragma"
          "--use-gitignore"
          "--exclude=*"
          "--extend-exclude=*"
          "--configuration=/tmp/policy"
          "--rules=/tmp/rules"
          "--profile=django"
          "--stdin-filename=other"
          "--version"
          "--help"
          "--"
          "other.html.njk"
        ];
  };
  rejects =
    phase: profile: value:
    !(tryEval (deepSeq (programs { ${phase}.${profile} = value; }).${phase} true)).success;
in
{
  djlint-adapter =
    toolPkgs.runCommandLocal "djlint-adapter"
      {
        nativeBuildInputs = [ renderPython ];
      }
      ''
        mkdir "$out"
        export COVERAGE_FILE="$TMPDIR/.coverage"
        status=0
        python -m coverage run --branch --parallel-mode --include=${../lib/djlint.py} \
          ${./djlint_adapter_test.py} ${../lib/djlint.py} ${lib.getExe toolPkgs.djlint} -v || status=$?
        python -m coverage combine
        python -m coverage report --show-missing --fail-under=100 > "$out/coverage.txt" || status=1
        cat "$out/coverage.txt"
        python -m coverage json -o "$out/coverage.json"
        exit "$status"
      '';
  djlint-schema =
    assert lib.elem (lib.getExe djlint.adapter)
      (project base).formatter.selection.formatters.nunjucks.options;
    assert lib.elem (lib.getExe toolPkgs.djlint)
      (project base).formatter.selection.formatters.nunjucks.options;
    assert lib.all
      (
        phase:
        lib.all (
          profile:
          lib.all (rejects phase profile) [
            true
            { }
            { includes = [ ]; }
            { includes = null; }
            { includes = [ "" ]; }
            { includes = "*.njk"; }
            { includes = [ 1 ]; }
            {
              includes = [ "*.njk" ];
              configFile = "/tmp/djlint.toml";
            }
          ]
        ) profiles
      )
      [
        "format"
        "lint"
      ];
    assert rejects "format" "nunjucks" {
      includes = [ "*.njk" ];
      options = [ ];
    };
    assert rejects "lint" "nunjucks" { includes = [ "nested/*.njk" ]; };
    # Disabled entries do not demand a pattern, config, package or runtime.
    assert
      (tryEval (
        deepSeq (programs {
          format.nunjucks = {
            enable = false;
            package = throw "unused";
            configFile = "/unused";
          };
          lint.jinja = false;
        }) true
      )).success;
    toolPkgs.runCommandLocal "djlint-schema" { } "touch $out";

  djlint-behavior =
    toolPkgs.runCommandLocal "djlint-behavior"
      {
        nativeBuildInputs = [
          renderPython
          toolPkgs.git
        ];
      }
      ''
        export XDG_CACHE_HOME="$TMPDIR/cache"
        python ${./djlint_test.py} ${toolPkgs.writeText "djlint-test-programs.json" (toJSON settings)} ${./fixtures/djlint}
        touch "$out"
      '';
}
