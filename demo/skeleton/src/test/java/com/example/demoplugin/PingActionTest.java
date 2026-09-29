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

import static com.google.common.truth.Truth.assertThat;
import static org.mockito.Mockito.when;

import com.google.gerrit.extensions.restapi.Response;
import com.google.gerrit.server.config.PluginConfig;
import com.google.gerrit.server.config.PluginConfigFactory;
import com.google.gerrit.server.project.ProjectResource;
import org.eclipse.jgit.lib.Config;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.mockito.Mock;
import org.mockito.junit.MockitoJUnitRunner;

@RunWith(MockitoJUnitRunner.class)
public class PingActionTest {
  private static final String PLUGIN = "demo-plugin";
  private static final String PROJECT = "team/some-project";

  @Mock private PluginConfigFactory cfgFactory;
  @Mock private ProjectResource project;

  private Config gerritConfig;

  @Before
  public void setUp() {
    gerritConfig = new Config();
    when(cfgFactory.getFromGerritConfig(PLUGIN))
        .thenReturn(PluginConfig.createFromGerritConfig(PLUGIN, gerritConfig));
    when(project.getName()).thenReturn(PROJECT);
  }

  @Test
  public void pingAnswersWithPluginProjectAndDefaultMessage() {
    Response<PingAction.PingInfo> response = newAction().apply(project);

    assertThat(response.statusCode()).isEqualTo(200);
    assertThat(response.value().plugin).isEqualTo(PLUGIN);
    assertThat(response.value().project).isEqualTo(PROJECT);
    assertThat(response.value().message).isEqualTo("pong");
  }

  @Test
  public void pingUsesConfiguredMessage() {
    gerritConfig.setString("plugin", PLUGIN, DemoPluginConfig.KEY_PING_MESSAGE, "ahoy");

    Response<PingAction.PingInfo> response = newAction().apply(project);

    assertThat(response.value().message).isEqualTo("ahoy");
  }

  private PingAction newAction() {
    return new PingAction(PLUGIN, new DemoPluginConfig(cfgFactory, PLUGIN));
  }
}
