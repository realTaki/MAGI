export {};

declare global {
  interface MagiDesktop {
    startLocal(): Promise<void>;
    windowControl(action: string): Promise<void>;
    showChooser(): Promise<void>;
  }

  interface Window {
    magiDesktop?: MagiDesktop;
  }
}
