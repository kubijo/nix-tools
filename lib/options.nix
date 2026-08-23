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

  fileCheckerOptions =
    {
      enable ? true,
      exclude ? [ ],
      includes ? null,
      configFile ? null,
      package ? null,
      exe ? null,
      extraOptions ? [ ],
      # One invocation for every matching path, instead of one per file.
      batch ? false,
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
        ;
    };

  # Splices destructure too — treefmt takes an unknown `exclude` without complaint.
  formatterSpecOptions =
    {
      command,
      includes,
      options ? [ ],
      exclude ? [ ],
      priority ? 0,
    }:
    {
      inherit
        command
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
    }:
    {
      inherit
        command
        includes
        options
        searchPaths
        batch
        ;
      excludes = exclude;
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
      batch ? true,
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
        ignoreLinks
        ;
    };

  # treefmt's `**/` needs at least one path segment: on 2.5, `**/*.json` skipped
  # `sub/nested.json` yet formatted `root.json`. A slashless pattern gets both forms.
  anywhere = pattern: [
    pattern
    "**/${pattern}"
  ];
  toTreefmtExcludes = lib.concatMap (p: if lib.hasInfix "/" p then [ p ] else anywhere p);

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
