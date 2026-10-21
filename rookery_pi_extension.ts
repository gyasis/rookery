// Rookery extension for the pi-family harnesses (pi, omp). Installed by `rookery init --harness pi|omp`.
// Gives pi/omp what Claude Code gets from its hooks, by calling the same CLI entry points:
//   session_start      -> rookery hook session-start  (stdin: source, session_id, cwd; no compact re-run)
//   before_agent_start -> rookery hook prompt         (stdin: prompt, session_id)
//   agent_end          -> rookery hook stop           (stdin: last_assistant_message, session_id)
// Hook stdout is added to the agent's context. A hook never throws and never blocks the agent
// beyond its 5 s timeout. Set ROOKERY_DEBUG=1 to see failures on stderr.
// Project name: ROOKERY_PROJECT, else the name baked in by `rookery init`, else the git top level.
// @ts-nocheck

import { spawn, execFileSync } from "node:child_process";
import * as path from "node:path";

const BAKED_PROJECT = "__ROOKERY_PROJECT__"; // replaced by `rookery init`; left as is when copied by hand
const PLACEHOLDER = "__ROOKERY_" + "PROJECT__"; // built in two pieces so init does not rewrite it
const TIMEOUT_MS = 5000;
const MAX_OUT = 8 * 1024; // bytes of hook output kept
const DRAIN_MS = 200; // wait this long for stdout to close after the child exits
const DEBUG = !!process.env.ROOKERY_DEBUG;

function debug(msg: string): void {
  if (DEBUG) process.stderr.write(`[rookery-ext] ${msg}\n`);
}

function projectName(cwd: string): string {
  const env = process.env.ROOKERY_PROJECT;
  if (env) return env;
  if (BAKED_PROJECT && BAKED_PROJECT !== PLACEHOLDER) return BAKED_PROJECT;
  try {
    const top = execFileSync("git", ["-C", cwd, "rev-parse", "--show-toplevel"], {
      encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (top) return path.basename(top);
  } catch {}
  return path.basename(cwd);
}

// Run `rookery hook <sub> --project P` with a JSON object on stdin; resolve to its stdout ("" on any failure).
// Output is capped at MAX_OUT; after the child exits stdout gets DRAIN_MS to close (a grandchild may hold it).
function runHook(sub: string, project: string, input: Record<string, unknown>): Promise<string> {
  return new Promise((resolve) => {
    let out = "";
    let done = false;
    let ok = false; // the child exited with code 0
    let drain: any;
    const finish = (text: string) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      clearTimeout(drain);
      try { child?.stdout?.destroy(); } catch {} // let go of a pipe a grandchild still holds
      resolve(text);
    };
    let child;
    try {
      child = spawn(process.env.ROOKERY_BIN || "rookery", ["hook", sub, "--project", project], {
        env: process.env, stdio: ["pipe", "pipe", "ignore"],
      });
    } catch (e) {
      debug(`${sub}: spawn failed: ${e}`);
      return resolve("");
    }
    const timer = setTimeout(() => {
      debug(`${sub}: timed out after ${TIMEOUT_MS} ms`);
      try { child.kill("SIGKILL"); } catch {}
      finish("");
    }, TIMEOUT_MS);
    child.on("error", (e) => { debug(`${sub}: ${e}`); finish(""); });
    child.stdout.on("data", (d) => {
      if (done) return;
      out += d;
      if (Buffer.byteLength(out) > MAX_OUT) {
        out = Buffer.from(out).subarray(0, MAX_OUT).toString("utf8");
        debug(`${sub}: output cut at ${MAX_OUT} bytes`);
        try { child.kill("SIGKILL"); } catch {}
        finish(out.trim());
      }
    });
    child.stdout.on("error", () => {});
    child.stdin.on("error", () => {});
    child.on("exit", (code) => {
      if (code !== 0) { debug(`${sub}: exit ${code}`); return finish(""); }
      ok = true;
      drain = setTimeout(() => finish(out.trim()), DRAIN_MS);
    });
    child.on("close", (code) => {
      if (code !== 0 && !ok) debug(`${sub}: exit ${code}`);
      finish(code === 0 || ok ? out.trim() : "");
    });
    try { child.stdin.end(JSON.stringify(input)); } catch (e) { debug(`${sub}: stdin: ${e}`); }
  });
}

function sessionId(ctx: any): string | undefined {
  try { return ctx?.sessionManager?.getSessionId?.(); } catch { return undefined; }
}

function transcriptPath(ctx: any): string | undefined {
  try {
    const f = ctx?.sessionManager?.getSessionFile?.();
    return typeof f === "string" && f ? f : undefined;
  } catch { return undefined; }
}

// Text of the last assistant message; "" when that turn was aborted or failed (nothing half-written is sent).
function lastAssistantText(messages: any[]): string | undefined {
  for (let i = (messages?.length ?? 0) - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.role !== "assistant") continue;
    if (m.stopReason === "aborted" || m.stopReason === "error") return "";
    const c = m.content;
    if (typeof c === "string") return c;
    if (Array.isArray(c)) return c.filter((p) => p?.type === "text").map((p) => p.text).join("\n");
    return "";
  }
  return undefined;
}

export default function (pi: any) {
  let pending = ""; // session-start output, delivered with the first turn (session_start cannot inject context)
  let cwd = process.cwd();
  let lastStopText: string | undefined; // text last sent to the stop hook in this user turn

  const startHook = async (source: string, ctx: any) => {
    try {
      cwd = ctx?.cwd || cwd;
      const text = await runHook("session-start", projectName(cwd), {
        source, session_id: sessionId(ctx), cwd,
      });
      if (text) pending = text;
    } catch (e) { debug(`session_start: ${e}`); }
  };

  pi.on("session_start", async (event: any, ctx: any) => {
    await startHook(event?.reason === "resume" ? "resume" : "startup", ctx);
  });

  pi.on("before_agent_start", async (event: any, ctx: any) => {
    try {
      if (event?.messageType === "custom") return; // not a user prompt; the prompt hook is for prompts
      cwd = ctx?.cwd || cwd;
      const text = await runHook("prompt", projectName(cwd), {
        prompt: event?.prompt ?? "", session_id: sessionId(ctx), transcript_path: transcriptPath(ctx),
      });
      lastStopText = undefined; // a new user turn: its block may repeat an earlier one
      const parts = [pending, text].filter(Boolean);
      pending = "";
      if (!parts.length) return;
      return { message: { customType: "rookery", content: parts.join("\n"), display: false } };
    } catch (e) { debug(`before_agent_start: ${e}`); }
  });

  pi.on("agent_end", async (event: any, ctx: any) => {
    try {
      if (event?.willRetry) return; // the turn is not over yet
      const text = lastAssistantText(event?.messages);
      if (text === undefined || text === lastStopText) return; // agent_end can repeat within one turn
      lastStopText = text;
      await runHook("stop", projectName(ctx?.cwd || cwd), {
        last_assistant_message: text, session_id: sessionId(ctx), transcript_path: transcriptPath(ctx),
      });
    } catch (e) { debug(`agent_end: ${e}`); }
  });
}
