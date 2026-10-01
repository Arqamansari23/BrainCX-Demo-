// One WebSocket to the API that reconnects on drop (adapted from the clinic app).
// Every CRM change, from the voice agent, a webhook or another tab, arrives here.

import { useEffect, useRef, useState } from "react";
import type { LiveEvent } from "./api";

export function useLiveUpdates(
  onEvent: (event: LiveEvent) => void,
  // Called when the server refuses the socket outright, which usually means the
  // session has ended (logout in another tab, or the cookie expired).
  onRefused?: () => void,
): boolean {
  const [connected, setConnected] = useState(false);
  const handlers = useRef({ onEvent, onRefused });
  handlers.current = { onEvent, onRefused };

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let attempt = 0;

    const open = () => {
      let opened = false;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${proto}//${location.host}/ws`);
      socket.onopen = () => {
        opened = true;
        attempt = 0;
        setConnected(true);
      };
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data);
        if (data.type === "crm") handlers.current.onEvent(data as LiveEvent); // ignore pings
      };
      socket.onclose = () => {
        setConnected(false);
        if (closed) return;
        if (!opened) handlers.current.onRefused?.();
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
