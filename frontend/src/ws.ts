import { useEffect } from "react";

export interface WsMessage {
  type: "agent_event" | "project_status";
  project_id: number;
  [key: string]: unknown;
}

/**
 * Subscribes to live pipeline updates over /api/v1/ws. If the socket never
 * connects or drops, falls back to calling onFallbackPoll on an interval so
 * the dashboard keeps working without live push updates.
 */
export function useLiveUpdates(onMessage: (msg: WsMessage) => void, onFallbackPoll: () => void): void {
  useEffect(() => {
    let socket: WebSocket | null = null;
    let pollInterval: number | null = null;
    let stopped = false;

    const startPolling = () => {
      if (pollInterval !== null || stopped) return;
      pollInterval = window.setInterval(onFallbackPoll, 4000);
    };

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`);
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        // ignore malformed frames
      }
    };
    socket.onerror = () => startPolling();
    socket.onclose = () => startPolling();

    return () => {
      stopped = true;
      socket?.close();
      if (pollInterval !== null) window.clearInterval(pollInterval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
