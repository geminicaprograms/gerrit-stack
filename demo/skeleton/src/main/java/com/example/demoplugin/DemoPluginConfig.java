// Copyright (C) 2026 gerrit-stack demo
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.example.demoplugin;

import com.google.gerrit.extensions.annotations.PluginName;
import com.google.gerrit.server.config.PluginConfig;
import com.google.gerrit.server.config.PluginConfigFactory;
import com.google.inject.Inject;
import com.google.inject.Singleton;

/**
 * Settings of the demo plugin, read from the {@code [plugin "demo-plugin"]} section of {@code
 * gerrit.config}.
 *
 * <pre>
 * [plugin "demo-plugin"]
 *   pingMessage = pong
 * </pre>
 *
 * <p>Each setting is one constant plus one getter; add new settings the same way.
 */
@Singleton
public class DemoPluginConfig {
  static final String KEY_PING_MESSAGE = "pingMessage";
  static final String DEFAULT_PING_MESSAGE = "pong";

  private final PluginConfig cfg;

  @Inject
  DemoPluginConfig(PluginConfigFactory cfgFactory, @PluginName String pluginName) {
    this.cfg = cfgFactory.getFromGerritConfig(pluginName);
  }

  /** Message returned by the {@code ping} REST view and SSH command. */
  public String pingMessage() {
    return cfg.getString(KEY_PING_MESSAGE, DEFAULT_PING_MESSAGE);
  }
}
