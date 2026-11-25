/// <reference types="vite/client" />

interface Window {
  /** wry's channel to webviewrs, which closes the window on the message `close`. */
  ipc?: { postMessage(message: string): void }
}
