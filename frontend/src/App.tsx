import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, BarChart3, Boxes, ClipboardList, FlaskConical, Home, LogOut, Network, RefreshCw,
  ShieldCheck, Tent, WifiOff,
} from "lucide-react";
import { FIELD_NODES, get, isLoggedIn, loginAll, logoutAll, NODE_META, NodeId, sessionFor } from "./api";
import { AppCtx, Button, Field, Input, Select, Toasts, usePoll } from "./ui";
import Overview from "./pages/Overview";
import Nodes from "./pages/Nodes";
import Needs from "./pages/Needs";
import Supplies from "./pages/Supplies";
import Shelters from "./pages/Shelters";
import People from "./pages/People";
import Tasks from "./pages/Tasks";
import SyncCenter from "./pages/SyncCenter";
import Conflicts from "./pages/Conflicts";
import Lab from "./pages/Lab";
import Analytics from "./pages/Analytics";

const PAGES = [
  { id: "overview", label: "Overview", icon: Home, el: Overview },
  { id: "needs", label: "Needs", icon: AlertTriangle, el: Needs },
  { id: "supplies", label: "Supplies & Allocation", icon: Boxes, el: Supplies },
  { id: "shelters", label: "Shelters", icon: Tent, el: Shelters },
  { id: "tasks", label: "Tasks & Teams", icon: ClipboardList, el: Tasks },
  { id: "people", label: "People (protected)", icon: ShieldCheck, el: People },
  { id: "conflicts", label: "Conflict Center", icon: Activity, el: Conflicts },
  { id: "sync", label: "Sync Center", icon: RefreshCw, el: SyncCenter },
  { id: "nodes", label: "Nodes", icon: Network, el: Nodes },
  { id: "lab", label: "Partition Lab", icon: FlaskConical, el: Lab },
  { id: "analytics", label: "Analytics", icon: BarChart3, el: Analytics },
];

function Login({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState("coordinator");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState<Record<string, string>>();
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const r = await loginAll(user, pass);
    setBusy(false);
    setMsg(r);
    if (Object.values(r).some((v) => v === "ok")) onDone();
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-4">
      <form onSubmit={submit} className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
        <div className="mb-5">
          <div className="text-2xl font-bold tracking-tight text-slate-900">RelayAid</div>
          <div className="text-sm text-slate-500">Coordinate without connection.</div>
        </div>
        <p className="mb-4 text-sm text-slate-600">
          Sign in to the local operator account on each field node. Each node checks the passphrase itself; for
          coordinators and responders it also unlocks that node's encryption key for protected person records.
        </p>
        <div className="space-y-3">
          <Field label="Operator role">
            <Select value={user} onChange={(e) => setUser(e.target.value)} options={["coordinator", "responder", "viewer"]} />
          </Field>
          <Field label="Passphrase" hint="Local demo default: relayaid-demo (set RELAYAID_OPERATOR_PASSPHRASE before provisioning to change it)">
            <Input type="password" value={pass} onChange={(e) => setPass(e.target.value)} autoFocus />
          </Field>
        </div>
        {msg && (
          <ul className="mt-3 space-y-1 text-xs">
            {Object.entries(msg).map(([n, v]) => (
              <li key={n} className={v === "ok" ? "text-emerald-700" : "text-red-700"}>{NODE_META[n as NodeId].label}: {v === "ok" ? "signed in" : v}</li>
            ))}
          </ul>
        )}
        <div className="mt-5"><Button kind="primary" type="submit" disabled={busy || !pass}>{busy ? "Signing in..." : "Sign in to nodes"}</Button></div>
      </form>
    </div>
  );
}

function NodeSwitcher({ node, setNode }: { node: NodeId; setNode: (n: NodeId) => void }) {
  const { data } = usePoll(async () => {
    const out: Record<string, any> = {};
    await Promise.all(FIELD_NODES.map(async (n) => {
      try { out[n] = await get(n, "/api/sim"); } catch { out[n] = null; }
    }));
    return out;
  }, [], 3000);
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Operate as node">
      {FIELD_NODES.map((n) => {
        const sim = data?.[n];
        const down = sim === null;
        const offline = sim?.offline;
        const partitioned = sim && !offline && sim.blocked_peers?.length > 0;
        const active = n === node;
        return (
          <button key={n} role="tab" aria-selected={active} onClick={() => setNode(n)}
            className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium ring-1 transition ${active ? "bg-slate-900 text-white ring-slate-900" : "bg-white text-slate-700 ring-slate-200 hover:bg-slate-50"}`}>
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: NODE_META[n].color }} />
            {NODE_META[n].label}
            {down ? <span className="text-xs text-red-400">down</span>
              : offline ? <WifiOff size={13} className="text-red-400" aria-label="offline" />
              : partitioned ? <span className={`text-[10px] ${active ? "text-amber-300" : "text-amber-700"}`}>partitioned</span> : null}
          </button>
        );
      })}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(isLoggedIn());
  const [page, setPage] = useState(() => location.hash.slice(1) || "overview");
  const [node, setNodeState] = useState<NodeId>(() => (sessionStorage.getItem("relayaid.node") as NodeId) || "alpha");
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const h = () => setPage(location.hash.slice(1) || "overview");
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  const setNode = (n: NodeId) => {
    setNodeState(n);
    try { sessionStorage.setItem("relayaid.node", n); } catch { /* ignore */ }
  };
  const toast = useCallback((msg: string, kind: "ok" | "error" | "info" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 9000 : 4000);
  }, []);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const Page = (PAGES.find((p) => p.id === page) || PAGES[0]).el;
  const s = sessionFor(node);

  return (
    <AppCtx.Provider value={{ node, setNode, toast, role: s?.role, tick, refresh }}>
      <div className="flex min-h-screen">
        <div className="hidden w-60 shrink-0 bg-slate-900 md:block">
        <aside className="sticky top-0 flex h-screen flex-col text-slate-300">
          <div className="px-5 pb-4 pt-5">
            <div className="text-lg font-bold tracking-tight text-white">RelayAid</div>
            <div className="text-xs text-slate-400">Coordinate without connection</div>
          </div>
          <nav className="flex-1 space-y-0.5 overflow-y-auto px-2" aria-label="Main">
            {PAGES.map((p) => (
              <a key={p.id} href={`#${p.id}`}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${page === p.id ? "bg-slate-800 font-medium text-white" : "hover:bg-slate-800/60 hover:text-white"}`}>
                <p.icon size={16} /> {p.label}
              </a>
            ))}
          </nav>
          <div className="border-t border-slate-800 p-3 text-xs text-slate-400">
            Signed in as <span className="font-medium text-slate-200">{s?.username || "-"}</span> on {NODE_META[node].label}
            <button className="mt-2 flex items-center gap-1.5 text-slate-300 hover:text-white" onClick={async () => { await logoutAll(); setAuthed(false); }}>
              <LogOut size={13} /> Sign out of all nodes
            </button>
          </div>
        </aside>
        </div>
        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur md:px-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs text-slate-500">Operating as</div>
                <div className="truncate text-sm font-semibold text-slate-800">{NODE_META[node].team}</div>
              </div>
              <NodeSwitcher node={node} setNode={setNode} />
            </div>
            <nav className="mt-2 flex gap-1 overflow-x-auto md:hidden" aria-label="Main mobile">
              {PAGES.map((p) => (
                <a key={p.id} href={`#${p.id}`} className={`whitespace-nowrap rounded-md px-2 py-1 text-xs ${page === p.id ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700"}`}>{p.label}</a>
              ))}
            </nav>
          </header>
          <main className="mx-auto max-w-7xl p-4 md:p-6">
            {!s && <div className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">You are not signed in to {NODE_META[node].label}. <button className="underline" onClick={() => setAuthed(false)}>Sign in again</button></div>}
            <Page />
          </main>
        </div>
      </div>
      <Toasts items={toasts} />
    </AppCtx.Provider>
  );
}
