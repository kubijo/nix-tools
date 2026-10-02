{ lib, toolPkgs }:
let
  inherit (builtins)
    functionArgs
    isAttrs
    isList
    isString
    match
    ;
  nodeReport = import ./outdated-node.nix { inherit lib toolPkgs; };
  metadataOptions =
    source:
    {
      package ? null,
      version ? null,
      versionCommand ? null,
      versionPattern ? null,
      provider ? null,
      repo ? null,
      project ? null,
      url ? null,
      tags ? false,
      tagPattern ? null,
      skip ? null,
    }:
    assert lib.assertMsg (
      package == null || lib.isDerivation package
    ) "outdated metadata ${source}: package must be a derivation";
    assert lib.assertMsg (
      skip == null || (isString skip && match "[[:space:]]*" skip == null)
    ) "outdated metadata ${source}: skip requires a nonempty reason";
    assert lib.assertMsg (
      skip == null
      || (
        tags == false
        && lib.all (v: v == null) [
          package
          version
          versionCommand
          versionPattern
          provider
          repo
          project
          url
          tagPattern
        ]
      )
    ) "outdated metadata ${source}: skip cannot be combined with release settings";
    let
      kind =
        if provider != null then
          provider
        else if repo != null then
          "github"
        else if url != null then
          "git"
        else
          null;
      current =
        if version != null then
          version
        else if package != null then
          package.version or null
        else
          null;
    in
    if skip != null then
      {
        name = source;
        inherit source skip;
      }
    else
      assert lib.assertMsg (
        if versionCommand != null then
          version == null && isList versionCommand && versionCommand != [ ] && lib.all isString versionCommand
        else
          isString current && current != ""
      ) "outdated metadata ${source}: provide a versioned package, version, or nonempty versionCommand";
      assert lib.assertMsg (
        versionPattern == null || (versionCommand != null && isString versionPattern)
      ) "outdated metadata ${source}: versionPattern requires versionCommand";
      assert lib.assertMsg (
        lib.isBool tags
        && (tagPattern == null || isString tagPattern)
        && (
          !(tags || tagPattern != null)
          || lib.elem kind [
            "github"
            "git"
          ]
        )
      ) "outdated metadata ${source}: invalid tags/tagPattern";
      assert lib.assertMsg (lib.elem kind [
        "github"
        "git"
        "pypi"
        "npm"
        "crates"
      ]) "outdated metadata ${source}: unsupported provider";
      assert lib.assertMsg (
        if kind == "github" then
          isString repo && repo != "" && project == null && url == null
        else if kind == "git" then
          isString url && url != "" && repo == null && project == null
        else
          isString project && project != "" && repo == null && url == null
      ) "outdated metadata ${source}: declare one explicit upstream source";
      {
        name = source;
        inherit source;
        provider = kind;
      }
      // (if versionCommand == null then { version = current; } else { inherit versionCommand; })
      // lib.optionalAttrs (versionPattern != null) { inherit versionPattern; }
      // lib.optionalAttrs (kind == "github") { inherit repo; }
      // lib.optionalAttrs (kind == "git") {
        inherit url;
      }
      // lib.optionalAttrs (lib.elem kind [
        "pypi"
        "npm"
        "crates"
      ]) { inherit project; }
      // lib.optionalAttrs (lib.elem kind [
        "npm"
        "crates"
      ]) { reporter = lib.getExe nodeReport; }
      // lib.optionalAttrs tags { tags = true; }
      // lib.optionalAttrs (tagPattern != null) { inherit tagPattern; };
  metadata =
    source: value:
    let
      opts = if lib.isDerivation value then { package = value; } else value;
    in
    assert lib.assertMsg (
      isAttrs opts && lib.all (key: (functionArgs (metadataOptions source)) ? ${key}) (lib.attrNames opts)
    ) "outdated metadata ${source}: unsupported option";
    metadataOptions source opts;

in
{
  inherit metadata;
}
