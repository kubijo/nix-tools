# Broken expected rewrite

This deliberately wrong expectation proves embedded pattern tests are mandatory.

```grit
language rust

`legacy_rust($value)` => `modern_rust($value)`
```

## Rejects an incorrect expected rewrite

```rust
fn main() {
    let answer = legacy_rust(1);
}
```

```rust
fn main() {
    let answer = unchanged_rust(1);
}
```
