// One WebSocket to the API that reconnects on drop (adapted from the clinic app).
// Every CRM change, from the voice agent, a webhook or another tab, arrives here.

import { useEffect, useRef, useState } from "react";
import type { LiveEvent } from "./api";

export function useLiveUpdates(onEvent: (event: LiveEvent) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let attempt = 0;

    const open = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${proto}//${location.host}/ws`);
      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data);
        if (data.type === "crm") handler.current(data as LiveEvent); // ignore pings
      };
      socket.onclose = () => {
        setConnected(false);
        if (closed) return;
        // Back off up to 10s so a restarting API isn't hammered.
        retry = setTimeout(open, Math.min(1000 * 2 ** attempt++, 10000));
      };
      socket.onerror = () => socket?.close();
    };

    open();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return connected;
}
