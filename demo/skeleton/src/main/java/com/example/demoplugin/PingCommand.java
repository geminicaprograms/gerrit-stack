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

import com.google.gerrit.server.project.ProjectState;
import com.google.gerrit.sshd.CommandMetaData;
import com.google.gerrit.sshd.SshCommand;
import com.google.inject.Inject;
import org.kohsuke.args4j.Argument;

/** {@code ssh -p 29418 <host> demo-plugin ping <project>} prints {@code pong <project>}. */
@CommandMetaData(name = "ping", description = "Print the ping message for a project")
final class PingCommand extends SshCommand {
  @Argument(index = 0, required = true, metaVar = "PROJECT", usage = "name of the project")
  private ProjectState projectState;

  private final DemoPluginConfig config;

  @Inject
  PingCommand(DemoPluginConfig config) {
    this.config = config;
  }

  @Override
  protected void run() {
    stdout.println(config.pingMessage() + " " + projectState.getName());
  }
}
