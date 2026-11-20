import { useState } from "react";
import { Play, Scissors, Server, Unplug } from "lucide-react";
import { ALL_NODES, FIELD_NODES, FieldNode, get, NODE_META, NodeId, patch, post, put } from "../api";
import { Badge, Button, Card, Field, Input, NodeChip, Select, Table, useApp } from "../ui";
import { ConvergenceBanner, useAllNodes } from "./Overview";

const POS: Record<NodeId, [number, number]> = { hub: [260, 45], alpha: [75, 215], bravo: [445, 215], charlie: [260, 305] };
const LINKS: [NodeId, NodeId][] = [["alpha", "bravo"], ["alpha", "charlie"], ["bravo", "charlie"], ["hub", "alpha"], ["hub", "bravo"], ["hub", "charlie"]];
const ORDERS: Record<string, [FieldNode, NodeId][]> = {
  "Ring  A→B, B→C, C→A": [["alpha", "bravo"], ["bravo", "charlie"], ["charlie", "alpha"]],
  "Reverse ring  C→B, B→A, A→C": [["charlie", "bravo"], ["bravo", "alpha"], ["alpha", "charlie"]],
  "Star via server": [["alpha", "hub"], ["bravo", "hub"], ["charlie", "hub"]],
  "Full mesh": [["alpha", "bravo"], ["alpha", "charlie"], ["bravo", "alpha"], ["bravo", "charlie"], ["charlie", "alpha"], ["charlie", "bravo"]],
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Sims = Record<string, any>;

async function readSims(): Promise<Sims> {
  const out: Sims = {};
  for (const n of FIELD_NODES) out[n] = await get(n, "/api/sim");
  return out;
}
function linkUp(sims: Sims, a: NodeId, b: NodeId) {
  const blocked = (x: NodeId, y: NodeId) => x !== "hub" && (sims[x]?.offline || sims[x]?.blocked_peers?.includes(y));
  return !blocked(a, b) && !blocked(b, a);
}
async function setLink(sims: Sims, a: NodeId, b: NodeId, up: boolean) {
  for (const [x, y] of [[a, b], [b, a]] as [NodeId, NodeId][]) {
    if (x === "hub") continue; // the server has no operator API; field nodes enforce the cut
    const cur: string[] = sims[x].blocked_peers || [];
    const next = up ? cur.filter((p) => p !== y) : Array.from(new Set([...cur, y]));
    sims[x] = await put(x, "/api/sim", { blocked_peers: next });
  }
}
async function applyGroups(groups: NodeId[][]) {
  for (const n of FIELD_NODES) {
    const g = groups.find((gr) => gr.includes(n)) || [n];
    await put(n, "/api/sim", { offline: false, blocked_peers: ALL_NODES.filter((x) => !g.includes(x)) });
  }
}
async function digests() {
  const out: Record<string, string> = {};
  for (const n of ALL_NODES) {
    try { out[n] = (await get(n, "/api/state/digest")).state; } catch { out[n] = "unreachable"; }
  }
  return out;
}

export default function Lab() {
  const { toast, refresh, role } = useApp();
  const { data: nodes, reload } = useAllNodes(2500);
  const sims: Sims = Object.fromEntries(FIELD_NODES.map((n) => [n, nodes?.[n]?.sim]).filter(([, s]) => s));
  const [busy, setBusy] = useState(false);
  const [order, setOrder] = useState(Object.keys(ORDERS)[0]);
  const [log, setLog] = useState<string[]>([]);
  const [result, setResult] = useState<any>();
  const isCoord = role === "coordinator";

  const say = (m: string) => setLog((l) => [...l, `${new Date().toLocaleTimeString()}  ${m}`]);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e: any) { toast(e.message, "error"); say(`error: ${e.message}`); } finally { setBusy(false); reload(); refresh(); }
  };

  const toggleLink = (a: NodeId, b: NodeId) => act(async () => {
    const s = await readSims();
    const up = linkUp(s, a, b);
    await setLink(s, a, b, !up);
    say(`${up ? "cut" : "restored"} link ${NODE_META[a].label} ↔ ${NODE_META[b].label}`);
  });

  const preset = (name: string, groups: NodeId[][]) => act(async () => { await applyGroups(groups); say(`preset: ${name}`); });

  const converge = () => act(async () => {
    const pairs = ORDERS[order];
    const t0 = performance.now();
    const rounds: any[] = [];
    let moved = 0, bytes = 0;
    for (let round = 1; round <= 6; round++) {
      let roundMoved = 0;
      for (const [a, b] of pairs) {
        const r = await post(a, "/api/sync/run", { peer_id: b, trigger: "lab" });
        roundMoved += (r.events_pulled || 0) + (r.events_pushed || 0);
        bytes += (r.bytes_sent || 0) + (r.bytes_received || 0);
        say(`round ${round}: ${NODE_META[a].label} ⇄ ${NODE_META[b].label}: ${r.status} (pulled ${r.events_pulled ?? 0}, pushed ${r.events_pushed ?? 0}, ${r.duplicates ?? 0} dup)`);
      }
      moved += roundMoved;
      const d = await digests();
      const fieldSame = new Set(FIELD_NODES.map((n) => d[n])).size === 1;
      rounds.push({ round, moved: roundMoved, digests: d });
      if (fieldSame && roundMoved === 0) {
        const ms = performance.now() - t0;
        setResult({ ok: true, ms, rounds: round, moved, bytes, digests: d });
        say(`converged: identical digests on all field nodes after ${round} round(s), ${moved} events moved, ${ms.toFixed(0)} ms`);
        return;
      }
    }
    const ms = performance.now() - t0;
    setResult({ ok: false, ms, rounds: rounds.length, moved, bytes, digests: rounds[rounds.length - 1].digests });
    say("not converged: some links are still cut (expected under a partition) - heal or change the order");
  });

  // ------------------------------------------------------------------ guided scenarios
  const quiet = async () => { for (const n of FIELD_NODES) await put(n, "/api/sim", { auto_sync: false }); };
  const heal = async () => { for (const n of FIELD_NODES) await put(n, "/api/sim", { offline: false, blocked_peers: [] }); };
  const syncPairs = async (pairs: [FieldNode, NodeId][]) => {
    for (const [a, b] of pairs) {
      const r = await post(a, "/api/sync/run", { peer_id: b, trigger: "lab" });
      say(`  sync ${NODE_META[a].label} ⇄ ${NODE_META[b].label}: ${r.status}, pulled ${r.events_pulled}, pushed ${r.events_pushed}`);
    }
  };
  const ring: [FieldNode, NodeId][] = [["alpha", "bravo"], ["bravo", "charlie"], ["charlie", "alpha"], ["alpha", "bravo"]];
  const checkDigests = async (label: string) => {
    const d = await digests();
    const ok = new Set(FIELD_NODES.map((n) => d[n])).size === 1;
    say(`${label}: ${FIELD_NODES.map((n) => `${NODE_META[n].label}=${d[n].slice(0, 8)}`).join("  ")}  → ${ok ? "CONVERGED" : "differ"}`);
    return ok;
  };

  const scenarios: Record<string, () => Promise<void>> = {
    "1 · Independent offline updates": async () => {
      await quiet();
      await applyGroups([["alpha"], ["bravo"], ["charlie"]]);
      say("all nodes disconnected from each other and from the server");
      await post("alpha", "/api/needs", { category: "medical", description: "Scenario 1: insulin for 3 patients", quantity: 3, unit: "doses", priority: "high" });
      say("Alpha (offline) recorded a need");
      const sh = (await get<any[]>("bravo", "/api/shelters"))[0];
      if (sh) { await post("bravo", `/api/shelters/${sh.shelter_id}/occupancy`, { delta: 7, reason: "scenario 1 arrivals" }); say(`Bravo (offline) checked 7 people into ${sh.name}`); }
      else { await post("bravo", "/api/shelters", { name: "Scenario 1 shelter", capacity: 50 }); say("Bravo (offline) registered a shelter"); }
      await post("charlie", "/api/supplies", { name: "Scenario 1 tarpaulins", kind: "quantity", quantity: 80, unit: "sheets" });
      say("Charlie (offline) registered supply inventory");
      await checkDigests("while partitioned");
      await heal(); say("reconnected all nodes");
      await syncPairs(ring);
      await checkDigests("after sync");
    },
    "2 · Conflicting edits": async () => {
      await quiet();
      await heal();
      const sh = await post<any>("alpha", "/api/shelters", { name: `Scenario 2 hall ${Date.now() % 1000}`, capacity: 80 });
      await syncPairs([["alpha", "bravo"], ["charlie", "alpha"]]);
      await applyGroups([["alpha"], ["bravo"], ["charlie"]]);
      say("partitioned; Alpha and Bravo edit the same shelter");
      await patch("alpha", `/api/shelters/${sh.shelter_id}`, { capacity: 60, conditions: "east wing roof leaking" });
      await patch("bravo", `/api/shelters/${sh.shelter_id}`, { capacity: 100, conditions: "extra tents pitched" });
      say("Alpha: capacity 60, conditions A · Bravo: capacity 100, conditions B");
      await heal();
      await syncPairs([["charlie", "bravo"], ["alpha", "charlie"], ["bravo", "alpha"]]);
      say("synced in order Charlie-Bravo, Alpha-Charlie, Bravo-Alpha");
      await checkDigests("after sync");
      const c = (await get<any[]>("charlie", "/api/conflicts")).filter((x) => x.entity_id === sh.shelter_id);
      c.forEach((x) => say(`  conflict on ${x.field}: ${x.status} (${x.policy}) candidates ${x.candidates.map((k: any) => `${k.origin}=${JSON.stringify(k.value)}`).join(" vs ")}`));
      say("capacity (critical) stays OPEN for a coordinator; conditions (LWW) auto-merged deterministically and recorded");
    },
    "3 · Double allocation attempt": async () => {
      await quiet();
      await heal();
      const id = `R-${Date.now() % 100000}`;
      await post("alpha", `/api/supplies?supply_id=${id}`, { name: `Rescue boat ${id}`, kind: "indivisible", unit: "boat" });
      say(`registered boat ${id}; exclusive allocation token held by Alpha`);
      await syncPairs([["alpha", "bravo"], ["alpha", "charlie"]]);
      await applyGroups([["alpha"], ["bravo"], ["charlie"]]);
      say("Alpha and Bravo disconnected");
      await post("alpha", `/api/supplies/${id}/allocate`, { amount: 1, note: "evacuate ward 3" });
      say("Alpha allocated the boat (holds the token) ✓");
      try {
        await post("bravo", `/api/supplies/${id}/allocate`, { amount: 1, note: "evacuate school" });
        say("!! Bravo allocation unexpectedly succeeded");
      } catch (e: any) { say(`Bravo allocation BLOCKED: ${e.message}`); }
      await heal();
      await syncPairs(ring);
      for (const n of FIELD_NODES) {
        const s = (await get<any[]>(n, "/api/supplies")).find((x) => x.supply_id === id);
        const active = s.allocations.filter((a: any) => a.status === "reserved");
        say(`  ${NODE_META[n].label}: ${active.length} active allocation(s) of ${id} by ${active.map((a: any) => a.node_id).join(",")}`);
      }
    },
    "4 · Repeated partitions": async () => {
      await quiet();
      await applyGroups([["alpha"], ["bravo"], ["charlie"]]);
      say("all disconnected");
      for (const n of FIELD_NODES) await post(n, "/api/needs", { category: "food", description: `Scenario 4: first update from ${NODE_META[n].label}` });
      await applyGroups([["alpha", "bravo"], ["charlie"]]);
      say("reconnected only Alpha and Bravo");
      await syncPairs([["alpha", "bravo"]]);
      await applyGroups([["alpha"], ["bravo"], ["charlie"]]);
      say("disconnected again; more updates");
      await post("alpha", "/api/needs", { category: "water", description: "Scenario 4: second update from Alpha" });
      await post("bravo", "/api/needs", { category: "water", description: "Scenario 4: second update from Bravo" });
      await checkDigests("while partitioned");
      await applyGroups([["bravo", "charlie"], ["alpha"]]);
      say("Charlie rejoins via Bravo");
      await syncPairs([["charlie", "bravo"]]);
      await heal();
      say("all links restored; repeated synchronization");
      await syncPairs([...ring, ...ring]);
      await checkDigests("final");
    },
  };

  const runScenario = (name: string) => act(async () => { setLog([]); say(`▶ Scenario ${name}`); await scenarios[name](); await sleep(100); say("done"); });

  const setCond = (n: FieldNode, patchBody: any) => act(async () => { await put(n, "/api/sim", patchBody); });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Partition lab</h1>
        <p className="max-w-3xl text-sm text-slate-500">Cut and restore links, take nodes offline, degrade connectivity, and drive synchronization in a chosen order. Every node enforces its own link state - a cut link produces no traffic, and the receiving side rejects a peer it has cut.</p>
      </div>
      {!isCoord && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">Network controls need a coordinator session.</div>}
      <ConvergenceBanner nodes={nodes} />
      <div className="grid gap-4 xl:grid-cols-5">
        <Card title="Topology" subtitle="Click a link to cut or restore it" className="xl:col-span-3">
          <svg viewBox="0 0 520 380" className="w-full" role="img" aria-label="Network topology">
            {LINKS.map(([a, b]) => {
              const up = Object.keys(sims).length === 3 && linkUp(sims, a, b);
              const [x1, y1] = POS[a], [x2, y2] = POS[b];
              return (
                <g key={a + b} onClick={() => isCoord && !busy && toggleLink(a, b)} className="cursor-pointer">
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="transparent" strokeWidth={22} />
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={up ? "#16a34a" : "#dc2626"} strokeWidth={up ? 3 : 2} strokeDasharray={up ? "" : "6 6"} />
                  {!up && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} textAnchor="middle" fontSize="11" fill="#b91c1c">✕ cut</text>}
                </g>
              );
            })}
            {ALL_NODES.map((n) => {
              const [x, y] = POS[n];
              const st = nodes?.[n];
              const off = n !== "hub" && sims[n]?.offline;
              return (
                <g key={n}>
                  <circle cx={x} cy={y} r={34} fill="white" stroke={NODE_META[n].color} strokeWidth={off ? 2 : 4} strokeDasharray={off ? "4 4" : ""} />
                  <text x={x} y={y + 5} textAnchor="middle" fontSize="13" fontWeight="600" fill="#0f172a">{NODE_META[n].label}</text>
                  <rect x={x - 52} y={y + 38} width={104} height={18} rx={4} fill="white" />
                  <text x={x} y={y + 51} textAnchor="middle" fontSize="11" fill="#475569">{st?.error ? "down" : off ? "offline" : `${st?.event_count ?? "-"} ev · ${st?.digest?.state?.slice(0, 6) ?? ""}`}</text>
                </g>
              );
            })}
          </svg>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button small disabled={!isCoord || busy} onClick={() => preset("full partition", [["alpha"], ["bravo"], ["charlie"]])}><Scissors size={13} /> Full partition</Button>
            <Button small disabled={!isCoord || busy} onClick={() => preset("Alpha+Bravo | Charlie", [["alpha", "bravo", "hub"], ["charlie"]])}>Isolate Charlie</Button>
            <Button small disabled={!isCoord || busy} onClick={() => preset("Alpha | Bravo+Charlie (no server)", [["alpha"], ["bravo", "charlie"]])}>Isolate Alpha</Button>
            <Button small disabled={!isCoord || busy} onClick={() => act(async () => { const s = await readSims(); for (const n of FIELD_NODES) await setLink(s, n, "hub", false); say("server unreachable from every node"); })}><Server size={13} /> Server down</Button>
            <Button small disabled={!isCoord || busy} onClick={() => act(async () => { await heal(); say("all links restored"); })}><Unplug size={13} /> Heal all</Button>
          </div>
        </Card>
        <Card title="Convergence runner" subtitle="Real sync sessions, measured wall-clock" className="xl:col-span-2">
          <div className="space-y-3">
            <Field label="Synchronization order"><Select value={order} onChange={(e) => setOrder(e.target.value)} options={Object.keys(ORDERS)} /></Field>
            <Button kind="primary" disabled={!isCoord || busy} onClick={converge}><Play size={14} /> Sync until converged</Button>
            {result && (
              <div className={`rounded-lg p-3 text-sm ring-1 ${result.ok ? "bg-emerald-50 ring-emerald-200" : "bg-amber-50 ring-amber-200"}`}>
                <div className="font-semibold">{result.ok ? "Converged" : "Not converged"}</div>
                <div className="tabular-nums text-slate-700">{result.ms.toFixed(0)} ms · {result.rounds} round(s) · {result.moved} events moved · {(result.bytes / 1024).toFixed(1)} KB on the wire</div>
                <div className="mono mt-1 text-xs">{Object.entries(result.digests).map(([n, d]: any) => `${n}:${String(d).slice(0, 8)}`).join("  ")}</div>
              </div>
            )}
          </div>
          <div className="mt-4 border-t border-slate-100 pt-3">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Guided scenarios (from the brief)</div>
            <div className="flex flex-col gap-1.5">
              {Object.keys(scenarios).map((s) => <Button key={s} small disabled={!isCoord || busy} onClick={() => runScenario(s)}>{s}</Button>)}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">Scenarios turn auto-sync off on the field nodes so each step is visible. Re-enable it below.</p>
          </div>
        </Card>
      </div>
      <Card title="Network conditions per node" subtitle="Weak links, short sync windows, duplicate and reordered delivery">
        <Table head={["Node", "Online", "Auto-sync", "Latency ms", "Loss %", "Batch", "Window batches", "Duplicate", "Shuffle", "Blocked peers"]}>
          {FIELD_NODES.map((n) => {
            const s = sims[n];
            if (!s) return null;
            return (
              <tr key={n}>
                <td className="px-2 py-2"><NodeChip node={n} /></td>
                <td className="px-2"><input type="checkbox" disabled={!isCoord} checked={!s.offline} onChange={(e) => setCond(n, { offline: !e.target.checked })} aria-label="online" /></td>
                <td className="px-2"><input type="checkbox" disabled={!isCoord} checked={s.auto_sync} onChange={(e) => setCond(n, { auto_sync: e.target.checked })} aria-label="auto sync" /></td>
                <td className="w-24 px-2"><Input type="number" min={0} defaultValue={s.latency_ms} onBlur={(e) => setCond(n, { latency_ms: Number(e.target.value) })} /></td>
                <td className="w-20 px-2"><Input type="number" min={0} max={95} defaultValue={Math.round(s.drop_rate * 100)} onBlur={(e) => setCond(n, { drop_rate: Number(e.target.value) / 100 })} /></td>
                <td className="w-20 px-2"><Input type="number" min={1} defaultValue={s.batch_size} onBlur={(e) => setCond(n, { batch_size: Number(e.target.value) })} /></td>
                <td className="w-20 px-2"><Input type="number" min={0} defaultValue={s.window_batches} onBlur={(e) => setCond(n, { window_batches: Number(e.target.value) })} title="0 = unlimited" /></td>
                <td className="px-2"><input type="checkbox" disabled={!isCoord} checked={s.duplicate_delivery} onChange={(e) => setCond(n, { duplicate_delivery: e.target.checked })} aria-label="duplicate delivery" /></td>
                <td className="px-2"><input type="checkbox" disabled={!isCoord} checked={s.shuffle} onChange={(e) => setCond(n, { shuffle: e.target.checked })} aria-label="shuffle" /></td>
                <td className="px-2">{s.blocked_peers.length ? s.blocked_peers.map((p: string) => <Badge key={p} tone="red">{p}</Badge>) : <span className="text-xs text-slate-400">none</span>}</td>
              </tr>
            );
          })}
        </Table>
        <div className="mt-2"><Button small disabled={!isCoord || busy} onClick={() => act(async () => { for (const n of FIELD_NODES) await post(n, "/api/sim/reset"); say("network conditions reset to defaults"); })}>Reset all conditions</Button></div>
      </Card>
      <Card title="Lab log">
        <pre className="mono max-h-80 overflow-y-auto whitespace-pre-wrap text-xs text-slate-700">{log.length ? log.join("\n") : "Actions and scenario steps appear here."}</pre>
      </Card>
    </div>
  );
}
