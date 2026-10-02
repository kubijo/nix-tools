{ lib, toolPkgs }:
let
  inherit (builtins)
    getContext
    head
    match
    storeDir
    ;
  adapter = toolPkgs.writeShellApplication {
    name = "djlint-policy";
    text = ''
      exec ${toolPkgs.python3}/bin/python ${./djlint.py} "$@"
    '';
  };
in
{
  profiles = [
    "angular"
    "askama"
    "django"
    "golang"
    "handlebars"
    "jinja"
    "liquid"
    "nunjucks"
    "tera"
  ];
  inherit adapter;
  defaults = profile: {
    package = toolPkgs.djlint;
    binary = "djlint";
    configFile = ../conf/djlint.toml;
    configFlag =
      file:
      let
        policy = "${file}";
        storeName = match "${storeDir}/[a-z0-9]{32}-([^/]+)" policy;
        name = if storeName == null then baseNameOf policy else head storeName;
        syntax =
          if name == "pyproject.toml" then
            "pyproject"
          else if lib.hasSuffix ".toml" name then
            "toml"
          else
            "json";
      in
      assert lib.assertMsg (getContext policy != { })
        "${profile}.configFile must be a Nix path or derivation output so the policy participates in the formatter cache key";
      [
        policy
        syntax
      ];
  };
}
