// pawtrace's front end: densk-core's workbench plus pawtrace's panels. `bun run build` writes the
// static bundle into the Python package (src/pawtrace/webui/app/), which `pawtrace serve` serves at /.
import react from "@vitejs/plugin-react";
import { pdfjsAssets } from "densk-core/vite";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { thirdPartyLicenses } from "./licenses.ts";

const here = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [
    react(),
    // pdf.js's fonts, cmaps and decoders under /pdfjs/ (no CDN), without Liberation Sans (GPL-2.0 with a
    // font exception): the build stays permissive-only, and PDFs get Helvetica/Arial from system fonts.
    pdfjsAssets({ liberationFonts: false }),
    thirdPartyLicenses({ root: here, core: here + "densk-core" }), // THIRD-PARTY-NOTICES.txt + licenses.json
  ],
  define: { "process.env.IS_PREACT": JSON.stringify("false") }, // Excalidraw
  build: { outDir: "../src/pawtrace/webui/app", emptyOutDir: true, chunkSizeWarningLimit: 4000 },
  // `bun run dev` against a running `pawtrace serve --port 8457` (open the page once with ?t= to get the cookie)
  server: { port: 8458, host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8457", "/f": "http://127.0.0.1:8457", "/dz": "http://127.0.0.1:8457" } },
});
