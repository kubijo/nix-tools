# Legacy CSS colors are forbidden

Legacy colors bypass the approved design-token palette.

```grit
language css

`color: legacy;`
```

## Detects a legacy color

```css
.example {
    color: legacy;
}
```

```css
.example {
    color: legacy;
}
```

## Allows an approved color

```css
.example {
    color: modern;
}
```
