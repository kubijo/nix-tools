rec {
  # Formatting a fixture would "fix" its input and rewrite its golden to match.
  fixtures = "tests/fixtures/**";

  exclude = [
    fixtures
    # Example inputs retain their contents and permissions.
    "examples/dirspec/tree/**"
    # Kept byte-exact as the Unlicense publishes it.
    "LICENSE.txt"
  ];
}
