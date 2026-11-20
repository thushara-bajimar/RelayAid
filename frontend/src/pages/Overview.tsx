import { CheckCircle2, CircleAlert } from "lucide-react";
import { ALL_NODES, fmtBytes, fmtTime, get, NODE_META, NodeId } from "../api";
import { Badge, Card, Empty, NodeChip, Stat, Table, useApp, usePoll } from "../ui";

export function useAllNodes(interval = 3000) {
  return usePoll(async () => {
    const out: Partial<Record<NodeId, any>> = {};
    await Promise.all(ALL_NODES.map(async (n) => {
      try { out[n] = await get(n, "/api/node"); } catch (e: any) { out[n] = { error: e.message }; }
    }));
    return out;
  }, [], interval);
}

export function ConvergenceBanner({ nodes }: { nodes: Partial<Record<NodeId, any>> | undefined }) {
  if (!nodes) return null;
  const live = ALL_NODES.filter((n) => nodes[n] && !nodes[n].error);
  const digests = live.map((n) => nodes[n].digest.state);
  const converged = live.length > 1 && new Set(digests).size === 1;
  const groups: Record<string, string[]> = {};
  live.forEach((n) => (groups[nodes[n].digest.state] ||= []).push(NODE_META[n].label));
  return (
    <div className={`flex flex-wrap items-center gap-3 rounded-xl p-4 ring-1 ${converged ? "bg-emerald-50 ring-emerald-200" : "bg-amber-50 ring-amber-200"}`}>
      {converged ? <CheckCircle2 className="text-emerald-700" /> : <CircleAlert className="text-amber-700" />}
      <div className="min-w-0 flex-1">
        <div className={`font-semibold ${converged ? "text-emerald-900" : "text-amber-900"}`}>
          {converged ? `All ${live.length} replicas hold identical state` : "Replicas currently differ (expected while partitioned or before sync)"}
        </div>
        <div className="text-xs text-slate-600">
          Computed from each node's SHA-256 digest of its materialized state, not a status flag.{" "}
          {Object.entries(groups).map(([d, ns]) => <span key={d} className="mr-3 mono">{d.slice(0, 8)}: {ns.join(", ")}</span>)}
        </div>
      </div>
    </div>
  );
}

export function NodeCard({ id, n }: { id: NodeId; n: any }) {
  const m = NODE_META[id];
  if (!n || n.error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4">
        <NodeChip node={id} />
        <div className="mt-2 text-sm text-red-800">{n?.error || "loading..."}</div>
      </div>
    );
  }
  const reachable = n.peers.filter((p: any) => p.link_up).length;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" style={{ borderTop: `3px solid ${m.color}` }}>
      <div className="flex items-center justify-between">
        <NodeChip node={id} />
        {n.sim.offline ? <Badge tone="red">offline</Badge> : n.sim.blocked_peers.length ? <Badge tone="amber">partitioned</Badge> : <Badge tone="green">connected</Badge>}
      </div>
      <div className="mt-1 text-xs text-slate-500">{n.team}</div>
      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs">
        <dt className="text-slate-500">Events</dt><dd className="text-right font-medium tabular-nums">{n.event_count}</dd>
        <dt className="text-slate-500">Lamport</dt><dd className="text-right mono">{n.lamport}</dd>
        <dt className="text-slate-500">Links up</dt><dd className="text-right">{reachable}/{n.peers.length}</dd>
        <dt className="text-slate-500">Last sync</dt><dd className="text-right">{fmtTime(n.last_sync_at)}</dd>
        <dt className="text-slate-500">State digest</dt><dd className="text-right mono">{n.digest.state.slice(0, 8)}</dd>
        <dt className="text-slate-500">Open conflicts</dt><dd className="text-right">{n.overview.open_conflicts}</dd>
      </dl>
      <div className="mono mt-2 truncate text-[11px] text-slate-500" title="version vector">vv {JSON.stringify(n.version_vector)}</div>
    </div>
  );
}

export default function Overview() {
  const { node, tick } = useApp();
  const { data: nodes } = useAllNodes();
  const mine = nodes?.[node];
  const ov = mine && !mine.error ? mine.overview : undefined;
  const { data: sessions } = usePoll(() => get<any[]>(node, "/api/sync/sessions?limit=8"), [node, tick], 4000);
  const { data: events } = usePoll(() => get<any[]>(node, "/api/events?limit=10"), [node, tick], 4000);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Coordination overview</h1>
        <p className="text-sm text-slate-500">Figures below are read from {NODE_META[node].label}'s own local database - what this team can see right now, connected or not.</p>
      </div>
      <ConvergenceBanner nodes={nodes} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {ALL_NODES.map((id) => <NodeCard key={id} id={id} n={nodes?.[id]} />)}
      </div>
      {ov && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Active needs" value={ov.active_needs} hint={`${ov.critical_needs} critical`} tone={ov.critical_needs ? "red" : undefined} />
          <Stat label="Supplies available" value={`${ov.supplies_available}/${ov.supplies}`} hint="resource lines" />
          <Stat label="Open shelters" value={ov.shelters_open} hint={`${ov.shelter_space} spaces free`} />
          <Stat label="Active tasks" value={ov.active_tasks} />
          <Stat label="People alerts" value={ov.people_alerts} hint={`${ov.people_unverified} unverified`} tone={ov.people_alerts ? "amber" : undefined} />
          <Stat label="Open conflicts" value={ov.open_conflicts} tone={ov.open_conflicts ? "red" : undefined} />
          <Stat label="Rights requests" value={ov.pending_requests} hint="pending" />
          <Stat label="Teams" value={ov.teams} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Recent synchronization" subtitle={`Sessions started by ${NODE_META[node].label}`}>
          {!sessions?.length ? <Empty>No sync sessions yet.</Empty> : (
            <Table head={["When", "Peer", "Status", "Events in/out", "Wire bytes"]}>
              {sessions.map((s) => (
                <tr key={s.session_id}>
                  <td className="px-2 py-1.5 text-xs text-slate-500">{fmtTime(s.started_at)}</td>
                  <td className="px-2 py-1.5"><NodeChip node={s.peer_id} small /></td>
                  <td className="px-2 py-1.5"><Badge tone={s.status === "success" ? "green" : s.status === "link_down" ? "gray" : s.status === "partial" ? "amber" : "red"}>{s.status}</Badge></td>
                  <td className="px-2 py-1.5 tabular-nums">{s.events_pulled} / {s.events_pushed}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtBytes(s.bytes_sent + s.bytes_received)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Card title="Recent events" subtitle="Append-only signed event log (person fields shown encrypted)">
          {!events?.length ? <Empty>No events yet.</Empty> : (
            <ul className="divide-y divide-slate-100 text-sm">
              {events.map((e) => (
                <li key={e.event_id} className="flex items-center gap-2 py-1.5">
                  <NodeChip node={e.origin} small />
                  <span className="font-medium text-slate-800">{e.type}</span>
                  <span className="mono truncate text-xs text-slate-500">{e.entity_id}</span>
                  <span className="ml-auto whitespace-nowrap text-xs text-slate-500">L{e.lamport} · {fmtTime(e.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
