import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const rootDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8010" } },
  build: {
    rollupOptions: {
      input: {
        main: `${rootDir}index.html`,
        componentLab: `${rootDir}component-lab.html`,
        homeConcept: `${rootDir}home-concept.html`,
        homeConceptV2: `${rootDir}home-concept-v2.html`,
        radio: `${rootDir}radio.html`,
        spendConcept: `${rootDir}spend-concept.html`,
      },
    },
  },
});
