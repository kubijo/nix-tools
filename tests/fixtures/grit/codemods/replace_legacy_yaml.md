# Replace legacy YAML keys

Replace the legacy data key while preserving its value and surrounding comments.

```grit
language yaml

`legacy: $value` => `modern: $value`
```

## Rewrites a legacy key

```yaml
# Keep this comment.
legacy: 8
```

```yaml
# Keep this comment.
modern: 8
```

## Leaves an approved key unchanged

```yaml
modern: 8
```
