{
  outputs = { self }:
    let
      inherit (builtins) attrNames readDir;
    in {
      files = attrNames (readDir self);
      source = self.outPath;
    };
}
