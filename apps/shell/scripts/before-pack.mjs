import { writeRepositoryMetadata } from '../repository-origin.mjs';

// Runs even when electron-builder is invoked directly rather than via build.mjs.
export default function beforePack(context) {
  const shellDirectory = context.packager.info.appDir;
  writeRepositoryMetadata({ shellDirectory, cwd: shellDirectory });
}
