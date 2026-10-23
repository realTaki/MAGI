/** Packaging and artifact verification must use the same exact runtime pin. */
export function packagedElectronVersion(metadata) {
  const version = metadata.devDependencies?.electron;
  if (typeof version !== "string" || !/^\d+\.\d+\.\d+$/.test(version)) {
    throw new Error("Electron must have an exact stable version pin");
  }
  if (metadata.build?.electronVersion !== version) {
    throw new Error("Packaged Electron version differs from the dependency pin");
  }
  return version;
}
