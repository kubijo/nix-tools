# Replace legacy CSS colors

Replace legacy colors with the approved design-token value.

```grit
language css

`color: legacy;` => `color: modern;`
```

## Rewrites a legacy color

```css
.example {
    color: legacy;
}
```

```css
.example {
    color: modern;
}
```

## Leaves an approved color unchanged

```css
.example {
    color: modern;
}
```
