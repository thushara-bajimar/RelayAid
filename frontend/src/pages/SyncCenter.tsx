import { RefreshCw } from "lucide-react";
import { fmtBytes, fmtTime, get, NODE_META, post } from "../api";
import { Badge, Button, Card, Empty, NodeChip, Stat, Table, useAction, useApp, usePoll } from "../ui";

export default function SyncCenter() {
  const { node, tick } = useApp();
  const { run, busy } = useAction();
  const { data: st } = usePoll(() => get<any>(node, "/api/node"), [node, tick], 2500);
  const { data: sessions } = usePoll(() => get<any[]>(node, "/api/sync/sessions?limit=40"), [node, tick], 3000);
  const { data: m } = usePoll(() => get<any>(node, "/api/metrics"), [node, tick], 4000);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Sync center · {NODE_META[node].label}</h1>
        <p className="max-w-3xl text-sm text-slate-500">Each session exchanges version vectors, pulls only the events this node lacks and pushes only what the peer lacks, in bounded, gzip-compressed, signed batches. Duplicates are ignored by event id; failed requests are retried with exponential backoff.</p>
      </div>
      {m && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Sessions" value={m.sessions} hint={`${m.sessions_link_down} skipped: link down`} />
          <Stat label="Success rate" value={m.success_rate == null ? "-" : `${(m.success_rate * 100).toFixed(0)}%`} hint={`${m.sessions_failed} failed, ${m.sessions_partial} partial`} />
          <Stat label="Events moved" value={m.events_transferred} hint={`${m.events_pulled} in · ${m.events_pushed} out`} />
          <Stat label="Wire bytes" value={fmtBytes(m.bytes_sent + m.bytes_received)} hint={m.compression_ratio ? `${m.compression_ratio}× smaller than raw JSON` : undefined} />
          <Stat label="Duplicates ignored" value={m.counters.duplicate_events_ignored || 0} />
          <Stat label="Retries" value={m.retries} hint={`${m.counters.invalid_events_rejected || 0} invalid events rejected`} />
        </div>
      )}
      <Card title="Peers" subtitle="Pending = events this node holds that the peer had not acknowledged at the last contact">
        {!st ? <Empty>Loading…</Empty> : (
          <Table head={["Peer", "Link", "Last success", "Pending events", "Failures", "Last error", ""]}>
            {st.peers.map((p: any) => (
              <tr key={p.node_id}>
                <td className="px-2 py-2"><NodeChip node={p.node_id} /> <span className="text-xs text-slate-500">{p.role === "server" ? "central server" : "peer"}</span></td>
                <td className="px-2">{p.link_up ? <Badge tone="green">up</Badge> : <Badge tone="red">down</Badge>}</td>
                <td className="px-2 text-xs">{fmtTime(p.last_success_at)}</td>
                <td className="px-2 tabular-nums">{p.pending_events}</td>
                <td className="px-2 tabular-nums">{p.consecutive_failures}</td>
                <td className="max-w-xs truncate px-2 text-xs text-slate-500" title={p.last_error || ""}>{p.last_error || "-"}</td>
                <td className="px-2 text-right">
                  <Button small disabled={busy} onClick={() => run(async () => {
                    const r = await post(node, "/api/sync/run", { peer_id: p.node_id });
                    if (r.status !== "success") throw new Error(`Sync with ${NODE_META[p.node_id as "alpha"].label}: ${r.status}${r.error ? ` - ${r.error}` : ""}`);
                    return r;
                  }, `Synced with ${NODE_META[p.node_id as "alpha"].label}`)}><RefreshCw size={12} /> Sync now</Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card title="Session log" subtitle="Measured per session">
        {!sessions?.length ? <Empty>No sessions yet.</Empty> : (
          <Table head={["When", "Peer", "Trigger", "Status", "Pulled", "Pushed", "Dup", "Batches", "Retries", "Wire (raw)", "Duration"]}>
            {sessions.map((s) => (
              <tr key={s.session_id} className={s.status === "link_down" ? "text-slate-400" : ""}>
                <td className="whitespace-nowrap px-2 py-1.5 text-xs">{fmtTime(s.started_at)}</td>
                <td className="px-2"><NodeChip node={s.peer_id} small /></td>
                <td className="px-2 text-xs">{s.trigger}</td>
                <td className="px-2"><Badge tone={s.status === "success" ? "green" : s.status === "link_down" ? "gray" : s.status === "partial" ? "amber" : "red"} title={s.error || ""}>{s.status}</Badge></td>
                <td className="px-2 tabular-nums">{s.events_pulled}</td>
                <td className="px-2 tabular-nums">{s.events_pushed}</td>
                <td className="px-2 tabular-nums">{s.duplicates}</td>
                <td className="px-2 tabular-nums">{s.batches}</td>
                <td className="px-2 tabular-nums">{s.retries}</td>
                <td className="whitespace-nowrap px-2 tabular-nums text-xs">{fmtBytes(s.bytes_sent + s.bytes_received)} ({fmtBytes(s.raw_bytes_sent + s.raw_bytes_received)})</td>
                <td className="px-2 tabular-nums text-xs">{s.duration_ms?.toFixed(1)} ms</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
