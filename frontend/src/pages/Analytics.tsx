import { useState } from "react";
import { ALL_NODES, fmtBytes, get, NODE_META, NodeId } from "../api";
import { Card, Empty, NodeChip, Stat, Table, useApp, usePoll } from "../ui";

function SessionChart({ sessions }: { sessions: any[] }) {
  // single series: wire bytes per sync session, oldest -> newest. Hover shows the full record.
  const [hover, setHover] = useState<number | null>(null);
  const data = sessions.filter((s) => s.status !== "link_down").slice(0, 40).reverse();
  if (!data.length) return <Empty>No completed sync sessions on this node yet.</Empty>;
  const W = 640, H = 200, P = { l: 52, r: 8, t: 10, b: 24 };
  const max = Math.max(...data.map((s) => s.bytes_sent + s.bytes_received), 1);
  const step = (W - P.l - P.r) / data.length;
  const bw = Math.max(3, Math.min(18, step - 2));
  const y = (v: number) => H - P.b - (v / max) * (H - P.t - P.b);
  const ticks = [0, max / 2, max];
  const h = hover != null ? data[hover] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Wire bytes per sync session">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="#e2e8f0" strokeWidth={1} />
            <text x={P.l - 6} y={y(t) + 3} textAnchor="end" fontSize="10" fill="#64748b">{fmtBytes(Math.round(t))}</text>
          </g>
        ))}
        {data.map((s, i) => {
          const v = s.bytes_sent + s.bytes_received;
          const x = P.l + i * step + (step - bw) / 2;
          const top = y(v);
          return (
            <g key={s.session_id} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={P.l + i * step} y={P.t} width={step} height={H - P.t - P.b} fill="transparent" />
              <path d={`M${x},${H - P.b} V${top + Math.min(4, H - P.b - top)} q0,-4 4,-4 h${bw - 8} q4,0 4,4 V${H - P.b} Z`}
                fill={s.status === "success" ? "#2a78d6" : "#eda100"} opacity={hover == null || hover === i ? 1 : 0.45} />
            </g>
          );
        })}
        <text x={P.l} y={H - 6} fontSize="10" fill="#64748b">older</text>
        <text x={W - P.r} y={H - 6} fontSize="10" fill="#64748b" textAnchor="end">newer</text>
      </svg>
      {h && (
        <div className="pointer-events-none absolute right-2 top-2 rounded-lg bg-white p-2 text-xs shadow-lg ring-1 ring-slate-200">
          <div className="font-medium text-slate-800">Session #{h.session_id} with {NODE_META[h.peer_id as NodeId]?.label}</div>
          <div className="text-slate-600">{h.status} · {h.events_pulled + h.events_pushed} events · {h.duration_ms.toFixed(1)} ms</div>
          <div className="text-slate-600">wire {fmtBytes(h.bytes_sent + h.bytes_received)} · raw {fmtBytes(h.raw_bytes_sent + h.raw_bytes_received)}</div>
        </div>
      )}
      <p className="mt-1 text-xs text-slate-500">Blue: complete session · amber: partial (window closed or interrupted; resumed next time).</p>
    </div>
  );
}

export default function Analytics() {
  const { node, tick } = useApp();
  const { data: all } = usePoll(async () => {
    const out: Partial<Record<NodeId, any>> = {};
    await Promise.all(ALL_NODES.map(async (n) => { try { out[n] = await get(n, "/api/metrics"); } catch { out[n] = null; } }));
    return out;
  }, [tick], 5000);
  const { data: sessions } = usePoll(() => get<any[]>(node, "/api/sync/sessions?limit=200"), [node, tick], 5000);
  const m = all?.[node];
  const rows = ALL_NODES.map((n) => [n, all?.[n]] as const).filter(([, v]) => v);
  const sum = (k: string) => rows.reduce((a, [, v]) => a + (v.counters?.[k] || 0), 0);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Analytics</h1>
        <p className="text-sm text-slate-500">Every number here is read from the nodes' own session logs and counters; nothing is simulated or estimated. Offline benchmark results are in TESTING_AND_EVALUATION.md.</p>
      </div>
      {m && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Sync success" value={m.success_rate == null ? "-" : `${(m.success_rate * 100).toFixed(0)}%`} hint={NODE_META[node].label} />
          <Stat label="Avg session" value={m.avg_session_ms ? `${m.avg_session_ms} ms` : "-"} />
          <Stat label="Bytes / event" value={m.avg_bytes_per_event ?? "-"} hint="on the wire" />
          <Stat label="Compression" value={m.compression_ratio ? `${m.compression_ratio}×` : "-"} />
          <Stat label="Duplicates handled" value={sum("duplicate_events_ignored")} hint="all nodes" />
          <Stat label="Allocations blocked" value={sum("allocations_blocked")} hint="all nodes" />
          <Stat label="Invalid events" value={sum("invalid_events_rejected")} hint="rejected, all nodes" />
          <Stat label="Projection rebuild" value={`${m.last_rebuild_ms} ms`} hint={`${m.event_count} events`} />
        </div>
      )}
      <Card title={`Wire bytes per sync session · ${NODE_META[node].label}`} subtitle="Incremental sync: after the first exchange, sessions carry only new events">
        <SessionChart sessions={sessions || []} />
      </Card>
      <Card title="Per-node comparison" subtitle="Table view of the replicated system">
        <Table head={["Node", "Events", "Sessions", "Success", "Events moved", "Wire sent / recv", "Retries", "Dup. ignored", "Alloc. blocked", "Rejected by validation", "Conflicts (open / resolved / auto)"]}>
          {rows.map(([n, v]) => (
            <tr key={n}>
              <td className="px-2 py-2"><NodeChip node={n} /></td>
              <td className="px-2 tabular-nums">{v.event_count}</td>
              <td className="px-2 tabular-nums">{v.sessions}</td>
              <td className="px-2 tabular-nums">{v.success_rate == null ? "-" : `${(v.success_rate * 100).toFixed(0)}%`}</td>
              <td className="px-2 tabular-nums">{v.events_transferred}</td>
              <td className="whitespace-nowrap px-2 tabular-nums text-xs">{fmtBytes(v.bytes_sent)} / {fmtBytes(v.bytes_received)}</td>
              <td className="px-2 tabular-nums">{v.retries}</td>
              <td className="px-2 tabular-nums">{v.counters.duplicate_events_ignored || 0}</td>
              <td className="px-2 tabular-nums">{v.counters.allocations_blocked || 0}</td>
              <td className="px-2 tabular-nums">{v.rejected_by_validation}</td>
              <td className="px-2 tabular-nums">{v.conflicts.open || 0} / {v.conflicts.resolved || 0} / {v.conflicts.auto_merged || 0}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
