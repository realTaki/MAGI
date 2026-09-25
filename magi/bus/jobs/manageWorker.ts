/**
 * Ask the manager to start, stop, or restart one worker.
 *
 * The job carries no configuration on purpose: the manager reads the workspace
 * settings itself, so whoever changes a setting only has to say "reconsider this
 * worker" instead of repeating the values back.
 */

export type ManageWorkerNotify = {
  worker: string;
  action: "start" | "stop" | "restart";
};
