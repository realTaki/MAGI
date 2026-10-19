/** Window navigation stays in Electron; source code never has to construct a file URL. */
export async function loadApp(win, ui) {
  if (ui.startsWith('http')) await win.loadURL(ui);
  else await win.loadFile(ui);
}

/** Called only after the renderer's existing reload confirmation. */
export async function reloadInterface(api, win) {
  if (!api || !win || win.isDestroyed()) throw new Error('Shell is not ready');
  const { ui } = await api.invoke('runtime.interface');
  if (typeof ui !== 'string' || !ui) throw new Error('Shell did not provide an interface');
  await loadApp(win, ui);
  return { ui };
}
