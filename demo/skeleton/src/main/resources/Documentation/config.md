Configuration
=============

All settings live in the `[plugin "demo-plugin"]` section of `gerrit.config`:

```
[plugin "demo-plugin"]
  pingMessage = pong
```

`pingMessage`
:	Message returned by the `ping` REST view and SSH command. Default: `pong`.
