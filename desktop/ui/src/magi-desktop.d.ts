export {};

declare global {
  interface MagiDesktop {
    windowControl(action: string): Promise<void>;
  }

  interface Window {
    magiDesktop?: MagiDesktop;
  }
}
