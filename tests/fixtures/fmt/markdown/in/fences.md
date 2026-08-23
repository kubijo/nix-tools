# Fences

Each block is formatted by the tool that formats a file of that language.

```nix
{ a=1;b   =2; }
```

```bash
if [ 1 ]; then
echo hi
fi
```

```json
{"z":1,"w":[1,2]}
```

```toml
k   =   1
```

A block that is not valid code is left alone, with a warning, rather than failing the run.

```json
{ this is not json
```

```
unlabelled, so no formatter claims it
```
