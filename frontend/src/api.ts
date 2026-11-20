// Thin client for the per-node REST APIs. Every node is an independent backend; the
// dashboard keeps one operator session (bearer token) per node in sessionStorage, which is
// cleared when the browser tab closes.

export const FIELD_NODES = ["alpha", "bravo", "charlie"] as const;
export const ALL_NODES = ["hub", ...FIELD_NODES] as const;
export type NodeId = (typeof ALL_NODES)[number];
export type FieldNode = (typeof FIELD_NODES)[number];

export const NODE_META: Record<NodeId, { label: string; team: string; color: string; soft: string }> = {
  hub: { label: "Server", team: "Central Coordination Server", color: "#64748b", soft: "#e2e8f0" },
  alpha: { label: "Alpha", team: "Rescue Team North", color: "#2a78d6", soft: "#dbeafe" },
  bravo: { label: "Bravo", team: "Rescue Team South", color: "#eb6834", soft: "#ffedd5" },
  charlie: { label: "Charlie", team: "Relief Coordination Team", color: "#1baf7a", soft: "#d1fae5" },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const KEY = "relayaid.sessions";
type Stored = Record<string, { token: string; role: string; username: string; can_decrypt: boolean }>;

function load(): Stored {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}
let sessions: Stored = load();
function save() {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(sessions));
  } catch {
    /* storage unavailable: sessions stay in memory */
  }
}

export function sessionFor(node: NodeId) {
  return sessions[node];
}
export function isLoggedIn() {
  return FIELD_NODES.some((n) => sessions[n]);
}

export async function req<T = any>(node: NodeId, method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const s = sessions[node];
  if (s) headers.Authorization = `Bearer ${s.token}`;
  let res: Response;
  try {
    res = await fetch(`/n/${node}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, `${NODE_META[node].label} is not reachable (process stopped?)`);
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    if (res.status === 401 && s && path !== "/api/auth/login") {
      delete sessions[node];
      save();
    }
    const detail = data?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
      : detail || `HTTP ${res.status}`;
    if (res.status === 502 || res.status === 504) throw new ApiError(0, `${NODE_META[node].label} is not reachable`);
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const get = <T = any>(n: NodeId, p: string) => req<T>(n, "GET", p);
export const post = <T = any>(n: NodeId, p: string, b: unknown = {}) => req<T>(n, "POST", p, b);
export const patch = <T = any>(n: NodeId, p: string, b: unknown) => req<T>(n, "PATCH", p, b);
export const put = <T = any>(n: NodeId, p: string, b: unknown) => req<T>(n, "PUT", p, b);

export async function loginAll(username: string, passphrase: string) {
  const results: Record<string, string> = {};
  for (const n of FIELD_NODES) {
    try {
      const r = await req<any>(n, "POST", "/api/auth/login", { username, passphrase });
      sessions[n] = { token: r.token, role: r.role, username: r.username, can_decrypt: r.can_decrypt };
      results[n] = "ok";
    } catch (e: any) {
      results[n] = e.message;
    }
  }
  save();
  return results;
}

export async function logoutAll() {
  for (const n of FIELD_NODES) {
    if (sessions[n]) await req(n, "POST", "/api/auth/logout").catch(() => undefined);
  }
  sessions = {};
  save();
}

// ---- convenience for the partition lab --------------------------------------------------
export async function setBlocked(node: FieldNode, blocked: string[]) {
  return put(node, "/api/sim", { blocked_peers: blocked });
}

export function fmtBytes(n?: number | null) {
  if (n == null) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function fmtTime(t?: string | null) {
  if (!t) return "never";
  const d = new Date(t.endsWith("Z") || t.includes("+") ? t : t + "Z");
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return d.toLocaleString();
}
