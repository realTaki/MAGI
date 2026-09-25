/** A scheduled task fired — or the operator started it by hand. */

export type RunTaskNotify = { task_id: number; manual?: boolean };
