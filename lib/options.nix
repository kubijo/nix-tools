{ lib }:
rec {
  toggle =
    schema: value:
    schema (if lib.isBool value then { enable = value; } else { enable = true; } // value);

  formatterOptions =
    {
      enable ? true,
      # Composes with the global excludes.
      exclude ? [ ],
      # null keeps the built-in globs.
      includes ? null,
      # null keeps the shipped config, or the tool's own default.
      configFile ? null,
      package ? null,
      # Wins over `package`, for a binary that is not the derivation's mainProgram.
      exe ? null,
      # Replaces the built-in argv wholesale.
      options ? null,
      extraOptions ? [ ],
      priority ? null,
    }:
    assert lib.assertMsg (!(options != null && configFile != null))
      "formatter: `options` replaces the whole argv, so `configFile` would be dropped — pass it inside `options`";
    {
      inherit
        enable
        exclude
        includes
        configFile
        package
        exe
        options
        extraOptions
        priority
        ;
    };

  # The wrapper depends on Grit's exact format/write argv and cannot safely expose
  # the generic argument-replacement escape hatches.
  gritFormatterOptions =
    {
      enable ? true,
      exclude ? [ ],
      includes ? null,
      package ? null,
      exe ? null,
      priority ? null,
    }:
    {
      inherit
        enable
        exclude
        includes
        package
        exe
        priority
        ;
      configFile = null;
      options = null;
      extraOptions = [ ];
    };

  fileCheckerOptions =
    {
      enable ? true,
      exclude ? [ ],
      includes ? null,
      configFile ? null,
      package ? null,
      exe ? null,
      extraOptions ? [ ],
      # One invocation containing every matching path. null inherits the built-in default.
      batch ? null,
      stdin ? "null",
    }:
    {
      inherit
        enable
        exclude
        includes
        configFile
        package
        exe
        extraOptions
        batch
        stdin
        ;
    };

  # Splices destructure too — treefmt takes an unknown `exclude` without complaint.
  formatterSpecOptions =
    {
      command,
      includes,
      # Required even when empty: custom tools must declare every non-file cache input.
      cacheInputs,
      options ? [ ],
      exclude ? [ ],
      priority ? 0,
      outdated ? null,
    }:
    assert lib.assertMsg (
      outdated == null || lib.isAttrs outdated
    ) "outdated metadata must be a package or attribute set";
    {
      inherit
        command
        cacheInputs
        includes
        options
        priority
        ;
      excludes = toTreefmtExcludes exclude;
    };

  checkerSpecOptions =
    {
      command,
      includes,
      options ? [ ],
      exclude ? [ ],
      searchPaths ? [ "." ],
      batch ? false,
      stdin ? "null",
      outdated ? null,
    }:
    assert lib.assertMsg (
      outdated == null || lib.isAttrs outdated
    ) "outdated metadata must be a package or attribute set";
    {
      inherit
        command
        includes
        options
        searchPaths
        batch
        stdin
        ;
      excludes = exclude;
    };

  projectCheckerSpecOptions =
    {
      command,
      options ? [ ],
      exclude ? [ ],
      outdated ? null,
    }:
    assert lib.assertMsg (
      outdated == null || lib.isAttrs outdated
    ) "outdated metadata must be a package or attribute set";
    {
      inherit command options;
      excludes = exclude;
    };

  debianCheckerOptions =
    {
      enable ? true,
      exclude ? [ ],
      configFile ? null,
      package ? null,
      exe ? null,
      extraOptions ? [ ],
    }:
    {
      inherit
        enable
        exclude
        configFile
        package
        exe
        extraOptions
        ;
    };

  linkCheckerOptions =
    {
      enable ? true,
      exclude ? [ ],
      includes ? null,
      configFile ? null,
      package ? null,
      exe ? null,
      extraOptions ? [ ],
      # null inherits the built-in's choice; a bool is an explicit override.
      batch ? null,
      stdin ? "null",
      # Matched against the link target, not the file holding it.
      ignoreLinks ? [ ],
    }:
    {
      inherit
        enable
        exclude
        includes
        configFile
        package
        exe
        extraOptions
        batch
        stdin
        ignoreLinks
        ;
    };

  checkOptions =
    {
      # Runs after the source copy, once in each exported check derivation.
      prepare ? "",
      runtimeInputs ? [ ],
    }:
    {
      inherit prepare runtimeInputs;
    };

  gritArgsOptions =
    {
      common ? [ ],
      check ? [ ],
      apply ? [ ],
    }:
    {
      inherit
        common
        check
        apply
        ;
    };

  gritOptions =
    {
      enable ? true,
      package ? null,
      profiles ? { },
    }:
    {
      inherit
        enable
        package
        profiles
        ;
    };

  gritProfileOptions =
    {
      patterns ? null,
      paths ? [ ],
      exclude ? [ ],
      gritArgs ? { },
      gate ? true,
    }:
    {
      inherit
        patterns
        paths
        exclude
        gate
        ;
      gritArgs = gritArgsOptions gritArgs;
    };

  astGrepProfileOptions =
    {
      configFile ? null,
      paths ? [ ],
      exclude ? [ ],
      gate ? true,
    }:
    {
      inherit
        configFile
        paths
        exclude
        gate
        ;
    };

  astGrepOptions =
    {
      enable ? true,
      package ? null,
      profiles ? { },
    }:
    {
      inherit
        enable
        package
        profiles
        ;
    };

  biomeFormatterOptions =
    {
      enable ? true,
      exclude ? [ ],
      includes ? null,
      configFile ? null,
      package ? null,
      exe ? null,
      options ? null,
      extraOptions ? [ ],
      priority ? null,
      organizeImports ? false,
    }:
    assert lib.assertMsg (!(options != null && configFile != null))
      "formatter: `options` replaces the whole argv, so `configFile` would be dropped — pass it inside `options`";
    assert lib.assertMsg (!(organizeImports && options != null))
      "biome formatter: `organizeImports` needs the typed `configFile` API so both phases use the same cache-visible config; do not replace `options`";
    {
      inherit
        enable
        exclude
        includes
        configFile
        package
        exe
        options
        extraOptions
        priority
        organizeImports
        ;
    };

  # treefmt's `**/` needs at least one path segment: on 2.5, `**/*.json` skipped
  # `sub/nested.json` yet formatted `root.json`. A slashless pattern gets both forms.
  anywhere = pattern: [
    pattern
    "**/${pattern}"
  ];
  toTreefmtExcludes = lib.concatMap (p: if lib.hasInfix "/" p then [ p ] else anywhere p);

  # A shared default keeps formatter and checker recognition aligned.
  xmlIncludes = [
    "*.xml"
    "*.gpx"
  ];

  # Skipped wherever they occur, not only at the root.
  defaultExcludeDirs = [
    ".git"
    ".direnv"
    ".tmp"
    # treefmt writes its eval-cache db here when XDG_CACHE_HOME points into the tree.
    ".cache"
    "target"
    "dist"
    "build"
    "node_modules"
    ".venv"
    "__pycache__"
  ];

  defaultExcludeFiles = [
    "*.lock"
    "package-lock.json"
    "pnpm-lock.yaml"
    "go.sum"
    # `go mod tidy` formats it, but has side effects beyond formatting.
    "go.mod"
    # Byte-exact by definition.
    "*.patch"
    "*.diff"
    # No formatter claims these.
    ".gitignore"
    ".gitattributes"
    ".gitmodules"
    ".editorconfig"
    ".tool-versions"
    ".hgignore"
    ".svnignore"
    # `cargo add` writes inline tables that taplo would restructure.
    "Cargo.toml"
    "result"
    "result-*"
  ];

  defaultExcludes = defaultExcludeFiles ++ lib.concatMap (d: anywhere "${d}/**") defaultExcludeDirs;
}
