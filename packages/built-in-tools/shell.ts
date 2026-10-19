/**
 * Shell access: one foreground command, plus background processes the model
 * can poll (`bash_output`) and stop (`bash_kill`) through the ShellManager.
 */

import type { ExecutableTool } from "@magi/bus";
import { toolchainEnv } from "@magi/runtime/toolchain.js";
import { stringArg } from "./args.js";
import { clipShellOutput, ShellManager } from "./shellManager.js";


export function shellTools(workspace: string, shells: ShellManager): ExecutableTool[] {
  return [
    {
      name: "bash", description: "Run a foreground bash command in the workspace. Returns stdout, stderr, and exit code.",
      input_schema: { type: "object", properties: {
        command: { type: "string" }, timeout: { type: "integer", minimum: 1, maximum: 600 },
        run_in_background: { type: "boolean" },
      }, required: ["command"] },
      async run(args, signal) {
        const command = stringArg(args, "command");
        if (args.run_in_background === true) {
          const shell = shells.start(command, workspace, toolchainEnv());
          return `Command started in background. Use bash_output to monitor (bash_id='${shell.id}').\n\nCommand: ${command}\nBash ID: ${shell.id}`;
        }
        const timeout = typeof args.timeout === "number" ? Math.max(1, Math.min(600, args.timeout)) : 120;
        try {
          return await shells.foreground(command, workspace, timeout * 1000, signal, toolchainEnv());
        } catch (error) {
          const failed = error as Error & { code?: number | string; stdout?: string; stderr?: string };
          return clipShellOutput(`exit_code: ${failed.code ?? "error"}\n${failed.stdout ?? ""}${failed.stderr ?? failed.message}`);
        }
      },
    },
    {
      name: "bash_output", description: "Read new output from a background bash process.",
      input_schema: { type: "object", properties: { bash_id: { type: "string" }, filter_str: { type: "string" } }, required: ["bash_id"] },
      async run(args) { return shells.read(stringArg(args, "bash_id"), typeof args.filter_str === "string" ? args.filter_str : undefined); },
    },
    {
      name: "bash_kill", description: "Terminate and forget a background bash process.",
      input_schema: { type: "object", properties: { bash_id: { type: "string" } }, required: ["bash_id"] },
      async run(args) { return shells.kill(stringArg(args, "bash_id")); },
    },
  ];
}
