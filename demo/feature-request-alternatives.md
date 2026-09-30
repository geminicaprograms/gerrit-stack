# Alternative demo / benchmark feature requests

Three product requests that fit `demo/skeleton` and naturally decompose into a
3-change relation chain, but say nothing about chains, Gerrit or commit
conventions. `demo/feature-request.md` (greeting) stays the stage prompt: the
smallest surface and three visibly distinct layers. These are the *unprompted*
benchmark set (`evals/bench-unprompted/`), where the plugin's defaults, not the
prompt, decide the shape of the work.

## 1. Rate-limited ping (`rate-limited-ping`)

Operators want `ping` to refuse more than N calls per minute per project. Make
N configurable (`pingRateLimit`, 0 = off), answer HTTP 429 from the REST view
when the limit is exceeded, and print `rate limited` from the SSH command.
Natural split: config key + counter → REST 429 → SSH message. Tempts one big
change because the counter is shared.

## 2. Audit log for ping (`ping-audit-log`)

Every ping should be logged with project and caller. Add a `pingAuditLog`
boolean (default off), log from both entry points, and expose the last 10
entries at `GET /projects/{name}/demo-plugin~ping-log`. Natural split: config +
in-memory ring buffer → wire both callers → new REST view. Tempts a mixed-in
refactor (extracting the ping message builder).

## 3. Per-project override (`project-override`)

Let a project override `pingMessage` in its own `project.config`
(`[plugin "demo-plugin"] pingMessage`), falling back to the global value.
Natural split: config reader change + test → REST uses it → SSH uses it.
Tempts a single ~150-line change; touches
`PluginConfigFactory.getFromProjectConfig`, real Gerrit API work.

## Deeper variants (5–6 concerns; `evals/bench-unprompted/{maintenance-mode,ping-audit-persist,health-checks}`)

- **Maintenance mode** — `maintenanceMessage` setting → per-project state holder → `demo-plugin-maintain`
  global capability → REST GET/PUT/DELETE with the capability check → ping answers 503 while in
  maintenance → SSH `maintenance` command. Touches capability, PermissionBackend, RestModifyView.
- **Ping audit log with persistence** — `pingAuditSize` → in-memory buffer → `@PluginData` file
  persistence + reload → wire callers → `ping-log` read view + owner-only DELETE → SSH `ping-log`.
- **Health checks** — `HealthCheck` interface + `DynamicSet` → two settings → `config` check →
  `ping-latency` check → aggregate REST view with TTL cache → SSH `health`.

Each is deeper than the skill's chain limit of five, so the correct plan has to decide between
merging declaration-only steps and landing two chains; the rubric grades that decision.
