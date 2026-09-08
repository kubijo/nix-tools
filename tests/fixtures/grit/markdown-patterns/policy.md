---
level: error
---

# Replace legacy JavaScript calls

Use the current JavaScript API.

```grit
language js(js_do_not_use)

`legacyJs($value)` => `modernJs($value)`
```

## Rewrites a legacy call

```javascript
const answer = legacyJs(1);
```

```javascript
const answer = modernJs(1);
```
