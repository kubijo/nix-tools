{
  lib,
  system,
  toolPkgs,
  mkFormatter,
  mkChecker,
  mkValidate,
  configure,
}:
let
  # A fixture directory is no git checkout, so treefmt needs a root marker. Deleted before
  # capture, so no golden carries it.
  marker = ".fixture-root";

  formatterFor =
    args:
    mkFormatter (
      args
      // {
        inherit system;
        treeRootFile = marker;
        exclude = (args.exclude or [ ]) ++ [ marker ];
      }
    );

  # Off-by-default language to the tool only it reaches. Absent: the biome ones, since
  # `json` shares that package, and rust/scss/svg, which demand a runtime already.
  poisonedBy = {
    caddyfile = "caddy";
    png = "oxipng";
    po = "gettext";
    protobuf = "buf";
    python = "ruff";
    sql = "sqlfluff";
  };

  workCopy = src: ''
    cp -r ${src} work
    chmod -R u+w work
    cd work
    touch ${marker}
    export HOME="$TMPDIR"
  '';
in
rec {
  inherit marker formatterFor;

  # Shared with `packages.golden`, so asserting and regenerating cannot drift apart.
  mkGolden =
    name: fixture: args:
    toolPkgs.runCommandLocal "golden-${name}" { } ''
      ${workCopy "${fixture}/in"}
      ${lib.getExe (formatterFor args)} --no-cache
      rm ${marker}
      cp -r . "$out"
    '';

  mkGoldenCheck =
    name: fixture: args:
    toolPkgs.runCommandLocal "fmt-${name}" { } ''
      ${toolPkgs.diffutils}/bin/diff --recursive --no-dereference \
        ${mkGolden name fixture args} ${fixture}/out
      touch "$out"
    '';

  # Idempotency: `--fail-on-change` compares mtime, so a needless rewrite fails here.
  mkStableCheck =
    name: fixture: args:
    toolPkgs.runCommandLocal "fmt-stable-${name}" { } ''
      ${workCopy "${fixture}/out"}
      ${lib.getExe (formatterFor args)} --ci
      touch "$out"
    '';

  mkChkCase =
    name: fixture: args:
    let
      expected = if lib.hasPrefix "fail-" name then "fail" else "pass";
    in
    assert lib.assertMsg (lib.hasPrefix "fail-" name || lib.hasPrefix "pass-" name)
      "checker fixture ${name}: name must begin with pass- or fail-, which is what states its expectation";
    toolPkgs.runCommandLocal "chk-${name}" { } ''
      cp -r ${fixture} work && chmod -R u+w work && cd work
      export HOME="$TMPDIR"

      if ${lib.getExe (mkChecker ({ inherit system; } // args))}; then
        actual=pass
      else
        actual=fail
      fi

      if [ "$actual" != ${expected} ]; then
        echo "${name}: expected the checker to ${expected}, but it did $actual" >&2
        exit 1
      fi
      touch "$out"
    '';

  # Output its own linter rejects is a fight between two configs, yamlfmt's indent against
  # yamllint's, that neither tool's tests would notice.
  mkAgreeCheck =
    golden:
    toolPkgs.runCommandLocal "agree" { } ''
      cp -r ${golden} work && chmod -R u+w work && cd work
      export HOME="$TMPDIR"
      ${lib.getExe (mkChecker {
        inherit system;
      })}
      touch "$out"
    '';

  # An off toggle must cost nothing: the defaults close over 455 MiB, and each language
  # switched on adds to it. Poisoning the tools none reach makes an eager table an error.
  laziness =
    let
      poisoned =
        toolPkgs
        // lib.genAttrs (lib.attrValues poisonedBy) (
          name: throw "${name} was evaluated though every formatter using it is off by default"
        );

      formatterWith =
        args:
        mkFormatter (
          {
            inherit system;
            toolPkgs = poisoned;
          }
          // args
        );

      # A poison its language no longer reaches would cover nothing, so each must still bite.
      reaches =
        toggle:
        !(builtins.tryEval (builtins.seq (formatterWith { ${toggle} = true; }).outPath true)).success;
      stale = lib.attrNames (lib.filterAttrs (toggle: _: !reaches toggle) poisonedBy);
    in
    assert lib.assertMsg (
      stale == [ ]
    ) "poisoned tools no longer reached by ${toString stale}, so laziness is no longer covered there";
    toolPkgs.runCommandLocal "laziness" { } ''
      test -x ${lib.getExe (formatterWith { })}
      touch "$out"
    '';

  # `biome migrate` exits 0 whether or not a migration is owed, so the verdict has to come
  # from its report. Without this, a nixpkgs bump moving biome past the config this library
  # ships is noticed by nobody.
  biomeConfig = toolPkgs.runCommandLocal "biome-config" { } ''
    mkdir work && cd work
    cp ${../conf/biome.json} biome.json
    export HOME="$TMPDIR"

    report=$(${lib.getExe toolPkgs.biome} migrate 2>&1 || true)
    case "$report" in
      *"no migration needed"*) touch "$out" ;;
      *)
        printf '%s\n' "$report" >&2
        exit 1
        ;;
    esac
  '';

  # A wrapper must read its tool and argv, not carry them: two differing only in an embedded
  # store path are the same size with the same mtime, which is all treefmt stats.
  wrappedArgv =
    let
      project = configure {
        inherit system toolPkgs;
        src = ./.;
        inherit (toolPkgs) nodejs;
        format.svg = {
          configFile = ./conf/yamlfmt-indent4.yml;
          extraOptions = [ "--pretty" ];
        };
      };
    in
    toolPkgs.runCommandLocal "wrapped-argv" { } ''
      config=$(grep -o '/nix/store/[a-z0-9]*-treefmt.toml' ${lib.getExe project.formatter} | head -1)
      entry=$(grep -A4 'formatter.svg' "$config")

      for want in --pretty yamlfmt-indent4.yml '"--"'; do
        case "$entry" in
          *"$want"*) ;;
          *)
            echo "treefmt hashes these options, so they must carry $want: $entry" >&2
            exit 1
            ;;
        esac
      done

      case "$entry" in
        *svgo-*) ;;
        *)
          echo "the tool must be in the options too, or a version bump misses the hash" >&2
          exit 1
          ;;
      esac

      wrapper=$(printf '%s' "$entry" | grep -o '/nix/store/[a-z0-9]*-svg-format')
      for baked in yamlfmt-indent4.yml svgo-; do
        if grep -q -- "$baked" "$wrapper/bin/svg-format"; then
          echo "the wrapper baked $baked in, hiding it from the cache key" >&2
          exit 1
        fi
      done

      touch "$out"
    '';

  # Nothing else keeps the formatter and the checker agreeing on scope.
  projectLayers =
    let
      project = configure {
        inherit system toolPkgs;
        src = ./.;
        exclude = [ "everywhere/**" ];
        format = {
          exclude = [ "formatter-only/**" ];
          yaml.exclude = [ "yaml-only/**" ];
        };
        lint.exclude = [ "checker-only/**" ];
      };

      seen = ''
        seen() {
          if [ "$(grep -c "$2" "$1")" -gt 0 ]; then echo yes; else echo no; fi
        }
        want() {
          if [ "$(seen "$1" "$2")" != "$3" ]; then
            echo "$4 $2: wanted $3" >&2
            exit 1
          fi
        }
      '';
    in
    toolPkgs.runCommandLocal "project-layers" { } ''
      ${seen}
      config=$(grep -o '/nix/store/[a-z0-9]*-treefmt.toml' ${lib.getExe project.formatter} | head -1)
      want "$config" everywhere yes "the formatter missed the project's exclude"
      want "$config" formatter-only yes "the formatter missed its own exclude"
      want "$config" yaml-only yes "the formatter missed a language's exclude"
      want "$config" checker-only no "the checker's exclude leaked into the formatter"

      checker=${project.apps.lint.program}
      want "$checker" everywhere yes "the checker missed the project's exclude"
      want "$checker" checker-only yes "the checker missed its own exclude"
      want "$checker" formatter-only no "the formatter's exclude leaked into the checker"

      touch "$out"
    '';

  # One pass must report every problem, so a failing step cannot swallow those behind it.
  validateSteps =
    let
      marking =
        failFast:
        mkValidate {
          inherit system failFast;
          steps = [
            "touch before"
            "false"
            "touch after"
          ];
        };
    in
    toolPkgs.runCommandLocal "validate-steps" { } ''
      mkdir work && cd work
      if ${lib.getExe (marking false)}; then
        echo "a failing step left the run green" >&2; exit 1
      fi
      if [ ! -e before ] || [ ! -e after ]; then
        echo "every step must run, so one failure cannot hide the rest" >&2; exit 1
      fi

      rm before after && mkdir ../fast && cd ../fast
      if ${lib.getExe (marking true)}; then
        echo "failFast left the run green" >&2; exit 1
      fi
      if [ ! -e before ]; then echo "failFast skipped a step before the failure" >&2; exit 1; fi
      if [ -e after ]; then echo "failFast ran a step past the failure" >&2; exit 1; fi

      touch "$out"
    '';

  # treefmt's cache key covers a formatter's options, never a config's contents, so one
  # that never reaches `options` makes editing it a silent no-op.
  # Leg 2 is a control: without it, leg 3 passes just as well with caching off.
  cacheKey =
    let
      indent2 = lib.getExe (formatterFor { });
      indent4 = lib.getExe (formatterFor {
        yaml.configFile = ./conf/yamlfmt-indent4.yml;
      });
    in
    toolPkgs.runCommandLocal "cachekey" { } ''
      mkdir work && cd work
      touch ${marker}
      printf 'a:\n      b: 1\n' > n.yaml
      export HOME="$TMPDIR" XDG_CACHE_HOME="$TMPDIR/cache"

      ${indent2}
      if [ "$(sed -n 2p n.yaml)" != "  b: 1" ]; then
        echo "leg 1: the shipped config never reached yamlfmt" >&2; exit 1
      fi

      # Same size and mtime, which is all treefmt stats, so a warm cache skips this file
      # and the edit below survives.
      touch -r n.yaml "$TMPDIR/stamp"
      printf '9' | dd of=n.yaml bs=1 seek=8 conv=notrunc status=none
      touch -r "$TMPDIR/stamp" n.yaml
      ${indent2}
      if [ "$(sed -n 2p n.yaml)" != "  b: 9" ]; then
        echo "leg 2: the cache was cold, so leg 3 would prove nothing" >&2; exit 1
      fi

      # Same cache, same size, same mtime — only the config differs.
      ${indent4}
      if [ "$(sed -n 2p n.yaml)" != "    b: 9" ]; then
        echo "leg 3: changing a config did not invalidate treefmt's cache" >&2; exit 1
      fi

      touch "$out"
    '';
}
