/**
 * streamRegistry.ts — shared background-stream survival.
 *
 * Both chat surfaces (regular chat + code agent) run LLM streams that must
 * survive the React view unmounting or switching conversations, and must
 * drive a "this conversation is streaming" indicator reliably.
 *
 * The registry lives at MODULE scope (outside React), keyed by conversation
 * id. A running stream's AbortController + evolving buffer live here, so:
 *   - switching conversations / tabs never aborts or loses the stream,
 *   - a freshly mounted view re-attaches by reading the current buffer,
 *   - the stream's own callbacks keep updating the buffer (and persist on
 *     done) even when no view is showing it,
 *   - the indicator subscribes to start/end events instead of guessing.
 *
 * Generic over the buffer type T because the two surfaces carry different
 * payloads (regular chat: streamed text + phase; code agent: turn array).
 */

export type LiveStream<T> = {
  id: string;
  controller: AbortController | null;
  buffer: T;
};

export type StreamRegistry<T> = {
  /** Register a newly started stream (marks the id as running). */
  start(id: string, controller: AbortController | null, buffer: T): void;
  /** Replace the buffer for an in-flight stream. Cheap, no notify — call it
   *  per token; the showing view updates React state itself. */
  update(id: string, buffer: T): void;
  /** Current live stream for an id, or undefined if none is running. */
  get(id: string): LiveStream<T> | undefined;
  /** Whether a stream is currently in flight for this id. */
  isRunning(id: string): boolean;
  /** Finish a stream (removes it, fires subscribers). Idempotent. */
  end(id: string): void;
  /** Subscribe to start/end events. Returns an unsubscribe fn. */
  subscribe(fn: () => void): () => void;
};

export function createStreamRegistry<T>(): StreamRegistry<T> {
  const runs = new Map<string, LiveStream<T>>();
  const listeners = new Set<() => void>();

  const notify = () => {
    // Copy first: a listener may unsubscribe during iteration.
    for (const fn of [...listeners]) {
      try {
        fn();
      } catch {
        /* a broken listener must not break the others */
      }
    }
  };

  return {
    start(id, controller, buffer) {
      if (!id) return;
      runs.set(id, { id, controller, buffer });
      notify();
    },
    update(id, buffer) {
      const run = runs.get(id);
      if (run) run.buffer = buffer;
    },
    get(id) {
      return runs.get(id);
    },
    isRunning(id) {
      return runs.has(id);
    },
    end(id) {
      if (runs.delete(id)) notify();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
}
