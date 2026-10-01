// Talk to the CRM assistant from the browser, built with LiveKit's Session APIs
// (https://docs.livekit.io/frontends/build/sessions/).
//
// The token comes from our own API (/api/livekit/token), which checks the login
// cookie and decides the room and which agent joins. The browser never holds a
// LiveKit secret and can't choose another agent.

import {
  BarVisualizer,
  RoomAudioRenderer,
  SessionProvider,
  TrackToggle,
  useAgent,
  useSession,
  useSessionMessages,
  type AgentState,
} from "@livekit/components-react";
import { TokenSource, Track } from "livekit-client";
import { useEffect, useRef, useState } from "react";
import { api, notifyUnauthorized } from "./api";

// Module scope so the token source (and its token cache) survives re-renders.
const tokenSource = TokenSource.endpoint("/api/livekit/token");

const STATE_TEXT: Partial<Record<AgentState, string>> = {
  connecting: "Connecting…",
  "pre-connect-buffering": "Listening…",
  initializing: "Starting…",
  idle: "Ready",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
  failed: "Couldn't reach the assistant",
  disconnected: "Disconnected",
};

export function VoicePanel() {
  const session = useSession(tokenSource, { agentName: "crm-agent" });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // start() waits for the agent and never settles if it doesn't join, so End
  // aborts it through this controller.
  const startAbort = useRef<AbortController | null>(null);

  const start = async () => {
    setError(null);
    setStarting(true);
    const controller = new AbortController();
    startAbort.current = controller;
    try {
      // Fetches a token, joins the room, turns on the mic and waits for the agent.
      await session.start({ signal: controller.signal });
    } catch (err) {
      if (controller.signal.aborted) return; // the user pressed End
      // The token request fails with a 401 if the session expired.
      const me = await api.get<{ logged_in: boolean }>("/api/auth/me").catch(() => null);
      if (me && !me.logged_in) {
        notifyUnauthorized();
        return;
      }
      setError(err instanceof Error ? err.message : "Could not start the voice session");
      await session.end();
    } finally {
      if (startAbort.current === controller) {
        startAbort.current = null;
        setStarting(false);
      }
    }
  };

  const end = async () => {
    startAbort.current?.abort();
    startAbort.current = null;
    setStarting(false);
    await session.end();
  };

  return (
    <SessionProvider session={session}>
      <div className="voice" data-lk-theme="default">
        {session.isConnected || starting ? (
          <VoiceSession onEnd={end} />
        ) : (
          <button className="btn talk" onClick={start}>
            🎙️ Talk to assistant
          </button>
        )}
        {error && <p className="voice-error">{error}</p>}
      </div>
      <RoomAudioRenderer />
    </SessionProvider>
  );
}

function VoiceSession({ onEnd }: { onEnd: () => void }) {
  const agent = useAgent();
  const { messages } = useSessionMessages();
  const recent = messages.slice(-4);
  const transcript = useRef<HTMLUListElement>(null);

  // Keep the newest line in view as replies stream in.
  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight });
  });

  return (
    <div className="voice-card">
      <div className="voice-head">
        <span className={`state-dot state-${agent.state}`} />
        {STATE_TEXT[agent.state] ?? agent.state}
      </div>
      <BarVisualizer className="voice-viz" track={agent.microphoneTrack} state={agent.state} barCount={7} />
      <ul className="transcript" ref={transcript}>
        {recent.length === 0 && <li className="muted">Try: “Move John Smith to Qualified and create a follow-up for tomorrow.”</li>}
        {recent.map((m) => (
          <li key={m.id} className={m.type === "userTranscript" ? "you" : "assistant"}>
            <b>{m.type === "userTranscript" ? "You" : "Assistant"}:</b> {m.message}
          </li>
        ))}
      </ul>
      {agent.failureReasons && (
        <p className="voice-error">
          {agent.failureReasons.join(" ")} Is the voice agent running? Start it with{" "}
          <code>uv run src/agent.py dev</code>, then press End and Talk again.
        </p>
      )}
      <div className="voice-actions">
        <TrackToggle source={Track.Source.Microphone} aria-label="Microphone on or off" />
        <button className="btn danger" onClick={onEnd}>
          End
        </button>
      </div>
    </div>
  );
}
