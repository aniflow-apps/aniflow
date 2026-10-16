import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * The build number, baked into the bundle.
 *
 * The badge in the sidebar asks the *server* which build it is, so it cannot
 * notice a browser serving an old bundle - which has happened here, and cost
 * two wrong diagnoses before someone thought to hard-reload. Comparing this
 * against the server's answer catches exactly that case.
 *
 * 0 when the marker is missing, so a build never invents a number.
 */
function buildNumber(): number {
  const here = dirname(fileURLToPath(import.meta.url));
  for (const marker of [resolve(here, "../BUILD"), "/BUILD"]) {
    try {
      const value = Number.parseInt(readFileSync(marker, "utf-8").trim(), 10);
      if (Number.isFinite(value)) return value;
    } catch {
      // Try the next location.
    }
  }
  return 0;
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __BUILD__: JSON.stringify(buildNumber()),
  },
  server: {
    proxy: {
      "/api": "http://localhost:9630",
    },
  },
});
