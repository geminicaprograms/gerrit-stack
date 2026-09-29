load("//tools/bzl:junit.bzl", "junit_tests")
load(
    "//tools/bzl:plugin.bzl",
    "PLUGIN_DEPS",
    "PLUGIN_TEST_DEPS",
    "gerrit_plugin",
)

gerrit_plugin(
    name = "demo-plugin",
    srcs = glob(["src/main/java/**/*.java"]),
    manifest_entries = [
        "Gerrit-PluginName: demo-plugin",
        "Gerrit-Module: com.example.demoplugin.Module",
        "Gerrit-SshModule: com.example.demoplugin.SshModule",
        "Implementation-Title: gerrit-stack demo plugin",
    ],
    resources = glob(["src/main/resources/**/*"]),
)

junit_tests(
    name = "demo_plugin_tests",
    size = "small",
    srcs = glob(["src/test/java/**/*.java"]),
    tags = ["demo-plugin"],
    deps = PLUGIN_DEPS + PLUGIN_TEST_DEPS + [
        ":demo-plugin__plugin",
    ],
)
