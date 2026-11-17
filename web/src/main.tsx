import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { configurePlatform, configureStorage, setButtons, setHost, startShell, startTheme, type DesktopBridge } from "densk-core";
import { App, pawtraceHost } from "./App";
import "densk-core/styles.css";
import "./pawtrace.css";

declare global {
  interface Window {
    /** The desktop app's preload (desktop/preload.js): title-bar mode and window facts. */
    pawtraceDesktop?: DesktopBridge & { mode?: string; version?: string };
  }
}

// pawtrace serve speaks densk-core's file API; pawtrace's own pages sit in the tree beside the files.
setHost(pawtraceHost());
configureStorage({ prefix: "pawtrace" });
// Public users click: a sidebar button and a markdown bottom bar with read/edit (densk-core buttons.ts).
setButtons(true);
configurePlatform({ desktop: window.pawtraceDesktop });
startShell();
startTheme();

// data-pt-device is pawtrace's name for this device's class; densk-core keeps its own
// data-nynx-device current (on resize too), so ours follows it.
const root = document.documentElement;
const syncDevice = () => {
  const device = root.dataset.nynxDevice;
  if (device && root.dataset.ptDevice !== device) root.dataset.ptDevice = device;
};
syncDevice();
new MutationObserver(syncDevice).observe(root, { attributes: true, attributeFilter: ["data-nynx-device"] });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
