import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ServerMsg } from "../types";
import type { AlertEngine } from "./alerts";
import { initialState, reducer } from "./store";

/** WebSocket connection to /ws/session with auto-reconnect. Routes alert messages to the alert engine. */
export function useSession(engine: AlertEngine) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const wsRef = useRef<WebSocket | null>(null);
  const quietRef = useRef(false);
  const engineRef = useRef(engine);
  engineRef.current = engine;

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      dispatch({ type: "conn", conn: "connecting" });
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/session`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        dispatch({ type: "conn", conn: "open" });
        ws.send(JSON.stringify({ type: "settings", quiet_mode: quietRef.current }));
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") return;
        let msg: ServerMsg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "alert") void engineRef.current.notify(msg.alert);
        dispatch({ type: "msg", msg, now: performance.now() });
      };
      ws.onclose = () => {
        dispatch({ type: "conn", conn: "closed" });
        if (!closed) timer = setTimeout(connect, Math.min(4000, 400 * 2 ** retry++));
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
    };
  }, []);

  const send = useCallback((obj: Record<string, unknown>): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(obj));
    return true;
  }, []);

  const sendAudio = useCallback((buf: ArrayBuffer) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 1_000_000) ws.send(buf);
  }, []);

  return {
    state,
    dispatch,
    send,
    sendAudio,
    setQuiet: (q: boolean) => {
      quietRef.current = q;
      send({ type: "settings", quiet_mode: q });
    },
  };
}
