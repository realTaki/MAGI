/**
 * Shell access: one foreground command, plus background processes the model
 * can poll (`bash_output`) and stop (`bash_kill`) through the ShellManager.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ExecutableTool } from "../bus/index.js";
import { stringArg } from "./args.js";
import { ShellManager, shellInvocation } from "./shellManager.js";

const runCommand = promisify(execFile);

export function shellTools(workspace: string, shells: ShellManager): ExecutableTool[] {
  return [
    {
      name: "bash", description: "Run a foreground bash command in the workspace. Returns stdout, stderr, and exit code.",
      input_schema: { type: "object", properties: {
        command: { type: "string" }, timeout: { type: "integer", minimum: 1, maximum: 600 },
        run_in_background: { type: "boolean" },
      }, required: ["command"] },
      async run(args) {
        const command = stringArg(args, "command");
        if (args.run_in_background === true) {
          const shell = shells.start(command, workspace);
          return `Command started in background. Use bash_output to monitor (bash_id='${shell.id}').\n\nCommand: ${command}\nBash ID: ${shell.id}`;
        }
        const timeout = typeof args.timeout === "number" ? Math.max(1, Math.min(600, args.timeout)) : 120;
        try {
          const [executable, ...parameters] = shellInvocation(command);
          const { stdout, stderr } = await runCommand(executable, parameters, { cwd: workspace, timeout: timeout * 1000, maxBuffer: 256 * 1024 });
          return `exit_code: 0\n${stdout}${stderr}`.slice(0, 8192);
        } catch (error) {
          const failed = error as Error & { code?: number | string; stdout?: string; stderr?: string };
          return `exit_code: ${failed.code ?? "error"}\n${failed.stdout ?? ""}${failed.stderr ?? failed.message}`.slice(0, 8192);
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
