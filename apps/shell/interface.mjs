/**
 * Only the navigation surface needed by the bootstrap helpers. Electron stays
 * a runtime dependency of the entry point, not of these independently tested helpers.
 * @typedef {{ isDestroyed: () => boolean, loadURL: (url: string) => Promise<unknown>, loadFile: (file: string) => Promise<unknown> }} InterfaceWindow
 * @typedef {{ invoke?: (method: "runtime.interface") => Promise<unknown> }} InterfaceApi
 */

/**
 * Window navigation stays in Electron; source code never has to construct a file URL.
 * @param {InterfaceWindow} win
 * @param {string} ui
 * @returns {Promise<void>}
 */
export async function loadApp(win, ui) {
  if (ui.startsWith('http')) await win.loadURL(ui);
  else await win.loadFile(ui);
}

/**
 * Point the open window at a rebuilt UI. `payload.ui` is the revision entry
 * after ReplaceSelfJob; otherwise ask the live backend for `runtime.interface`.
 * @param {InterfaceApi | null | undefined} api
 * @param {InterfaceWindow | null | undefined} win
 * @param {{ ui?: unknown } | null | undefined} payload
 * @returns {Promise<{ ui: string }>}
 */
export async function applyInterfaceUpdate(api, win, payload) {
  if (!win || win.isDestroyed()) throw new Error('Shell is not ready');
  const direct = typeof payload?.ui === 'string' ? payload.ui.trim() : '';
  const response = direct ? undefined : await api?.invoke?.('runtime.interface');
  const ui = direct || (response && typeof response === 'object' && 'ui' in response ? response.ui : undefined);
  if (typeof ui !== 'string' || !ui) throw new Error('Shell did not provide an interface');
  await loadApp(win, ui);
  return { ui };
}

/**
 * @param {InterfaceApi | null | undefined} api
 * @param {InterfaceWindow | null | undefined} win
 * @returns {Promise<{ ui: string }>}
 */
export async function reloadInterface(api, win) {
  return applyInterfaceUpdate(api, win, {});
}
