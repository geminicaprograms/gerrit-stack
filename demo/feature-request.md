Feature request: greeting for demo-plugin

We want demo-plugin to greet a project the same way it already answers `ping`.

1. REST: `GET /projects/{name}/demo-plugin~greeting` returns `{"greeting":"<prefix>, <project>!"}`. The prefix comes from `[plugin "demo-plugin"] greetingPrefix` in gerrit.config, default `Hello`.
2. SSH: `demo-plugin greet <project>` prints the same greeting.
3. A project can override the prefix in its own project.config (`[plugin "demo-plugin"] greetingPrefix`); the server value stays the default.

When it is done, push the work to Gerrit for review.
