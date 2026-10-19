/** Window navigation stays in Electron; source code never has to construct a file URL. */
export async function loadApp(win, ui) {
  if (ui.startsWith('http')) await win.loadURL(ui);
  else await win.loadFile(ui);
}

/**
 * Point the open window at a rebuilt UI. `payload.ui` is the revision entry
 * after ReplaceSelfJob; otherwise ask the live backend for `runtime.interface`.
 */
export async function applyInterfaceUpdate(api, win, payload) {
  if (!win || win.isDestroyed()) throw new Error('Shell is not ready');
  const direct = typeof payload?.ui === 'string' ? payload.ui.trim() : '';
  const ui = direct || (await api?.invoke?.('runtime.interface'))?.ui;
  if (typeof ui !== 'string' || !ui) throw new Error('Shell did not provide an interface');
  await loadApp(win, ui);
  return { ui };
}

export async function reloadInterface(api, win) {
  return applyInterfaceUpdate(api, win, {});
}
