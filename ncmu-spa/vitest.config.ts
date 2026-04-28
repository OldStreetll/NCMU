import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./tests/setup.ts"],
      css: false,
      // Playwright e2e specs run via `npm run test:e2e` (mcr image); their
      // `test.describe()` can't be loaded by Vitest. Keep them out of unit
      // test discovery.
      exclude: ["**/node_modules/**", "**/dist/**", "tests/e2e/**"],
    },
  }),
);
