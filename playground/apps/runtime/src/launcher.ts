import { Bus } from "../bus/index.js";
import { Caller } from "../caller/worker.js";
import { DemoProvider } from "../provider/worker.js";
import { ResultReader } from "../result-reader/worker.js";

export type PlaygroundRuntime = {
  bus: Bus;
  caller: Caller;
  provider: DemoProvider;
  reader: ResultReader;
  shutdown(): Promise<void>;
};

export async function launchPlayground(workspace: string): Promise<PlaygroundRuntime> {
  const bus = await Bus.open(workspace);
  const caller = new Caller();
  const provider = new DemoProvider();
  const reader = new ResultReader();
  const callerBus = bus.forWorker(caller.workerName, caller.requiredSlots);
  const providerBus = bus.forWorker(provider.workerName, provider.requiredSlots);
  const readerBus = bus.forWorker(reader.workerName, reader.requiredSlots);
  if (!callerBus || !providerBus || !readerBus) {
    bus.close();
    throw new Error("playground slots are unavailable");
  }
  try {
    await caller.attach(callerBus);
    await provider.attach(providerBus);
    await reader.attach(readerBus);
  } catch (error) {
    await reader.detach();
    await provider.detach();
    await caller.detach();
    bus.close();
    throw error;
  }
  return {
    bus,
    caller,
    provider,
    reader,
    shutdown: async () => {
      await reader.detach();
      await caller.detach();
      await provider.detach();
      bus.close();
    },
  };
}
