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

import static com.google.gerrit.server.project.ProjectResource.PROJECT_KIND;

import com.google.gerrit.extensions.restapi.RestApiModule;
import com.google.inject.AbstractModule;

/**
 * Guice module of the demo plugin ({@code Gerrit-Module}).
 *
 * <p>Binds the plugin configuration and the REST endpoints under {@code
 * /projects/{name}/demo-plugin~<view>}. New REST views are registered in the nested {@link
 * RestApiModule}.
 */
public class Module extends AbstractModule {
  @Override
  protected void configure() {
    bind(DemoPluginConfig.class);

    install(
        new RestApiModule() {
          @Override
          protected void configure() {
            get(PROJECT_KIND, "ping").to(PingAction.class);
          }
        });
  }
}
