import { Bus } from "../src/index.js";
import { DemoProvider } from "./provider.js";

export type DemoRuntime = {
  bus: Bus;
  provider: DemoProvider;
  shutdown(): Promise<void>;
};

export async function launchDemo(workspace: string): Promise<DemoRuntime> {
  const bus = await Bus.open(workspace);
  const provider = new DemoProvider();
  const providerBus = bus.forWorker(provider.workerName, provider.requiredSlots);
  if (!providerBus) throw new Error("provider slots are unavailable");
  await provider.attach(providerBus);
  return {
    bus,
    provider,
    shutdown: async () => {
      await provider.detach();
      bus.close();
    },
  };
}
