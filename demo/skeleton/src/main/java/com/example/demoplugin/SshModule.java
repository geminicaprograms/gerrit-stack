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
import com.google.gerrit.sshd.PluginCommandModule;
import com.google.inject.Inject;

/**
 * SSH commands of the demo plugin ({@code Gerrit-SshModule}); every command is reachable as {@code
 * ssh -p 29418 <host> demo-plugin <command>}.
 */
public class SshModule extends PluginCommandModule {
  @Inject
  SshModule(@PluginName String pluginName) {
    super(pluginName);
  }

  @Override
  protected void configureCommands() {
    command(PingCommand.class);
  }
}
