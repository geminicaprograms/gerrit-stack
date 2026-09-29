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
import com.google.gerrit.extensions.restapi.Response;
import com.google.gerrit.extensions.restapi.RestReadView;
import com.google.gerrit.server.project.ProjectResource;
import com.google.inject.Inject;
import com.google.inject.Singleton;

/**
 * {@code GET /projects/{name}/demo-plugin~ping}.
 *
 * <p>Answers {@code {"plugin":"demo-plugin","project":"<name>","message":"pong"}}; the message
 * comes from {@link DemoPluginConfig#pingMessage()}.
 */
@Singleton
public class PingAction implements RestReadView<ProjectResource> {
  /** JSON body of the ping response. */
  public static class PingInfo {
    public String plugin;
    public String project;
    public String message;

    PingInfo(String plugin, String project, String message) {
      this.plugin = plugin;
      this.project = project;
      this.message = message;
    }
  }

  private final String pluginName;
  private final DemoPluginConfig config;

  @Inject
  PingAction(@PluginName String pluginName, DemoPluginConfig config) {
    this.pluginName = pluginName;
    this.config = config;
  }

  @Override
  public Response<PingInfo> apply(ProjectResource rsrc) {
    return Response.ok(new PingInfo(pluginName, rsrc.getName(), config.pingMessage()));
  }
}
