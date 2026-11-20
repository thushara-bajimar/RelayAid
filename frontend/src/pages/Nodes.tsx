import { ALL_NODES, FIELD_NODES, fmtTime, NODE_META, NodeId, post, put } from "../api";
import { Badge, Button, Card, NodeChip, Table, useAction, useApp } from "../ui";
import { useAllNodes } from "./Overview";

export default function Nodes() {
  const { role } = useApp();
  const { run, busy } = useAction();
  const { data: nodes, reload } = useAllNodes(2500);
  const coord = role === "coordinator";
  const act = (fn: () => Promise<unknown>, msg: string) => run(fn, msg).then(reload);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Nodes</h1>
        <p className="max-w-3xl text-sm text-slate-500">Each node is a separate process with its own SQLite file, Ed25519 identity, Lamport clock and version vector. The server is an optional relay: field nodes never need it to record or update data.</p>
      </div>
      <Card title="Node registry">
        <Table head={["Node", "Role", "Endpoint", "Database", "Events", "Version vector", "Server link", "Peer links", "Last sync", "Simulate"]}>
          {ALL_NODES.map((id) => {
            const n = nodes?.[id];
            if (!n || n.error) return (
              <tr key={id}><td className="px-2 py-2"><NodeChip node={id} /></td><td colSpan={9} className="px-2 text-sm text-red-700">{n?.error || "loading"}</td></tr>
            );
            const hub = n.peers.find((p: any) => p.node_id === "hub");
            const peers = n.peers.filter((p: any) => p.role === "node");
            const field = id !== "hub";
            return (
              <tr key={id} className="align-top">
                <td className="px-2 py-2"><NodeChip node={id} /><div className="mt-0.5 text-xs text-slate-500">{n.team}</div></td>
                <td className="px-2 py-2 text-xs">{n.role === "server" ? "central server" : "field node"}</td>
                <td className="mono px-2 py-2 text-xs">{n.url}</td>
                <td className="px-2 py-2 text-xs"><span className="mono">{n.db_path}</span> {n.db_ok ? <Badge tone="green">ok</Badge> : <Badge tone="red">check failed</Badge>}</td>
                <td className="px-2 py-2 tabular-nums">{n.event_count}</td>
                <td className="mono px-2 py-2 text-xs">{Object.entries(n.version_vector).map(([k, v]) => `${k}:${v}`).join(" ") || "{}"}</td>
                <td className="px-2 py-2">{!field ? "-" : hub?.link_up ? <Badge tone="green">reachable</Badge> : <Badge tone="red">unreachable</Badge>}</td>
                <td className="px-2 py-2">
                  <div className="flex flex-wrap gap-1">{peers.map((p: any) => <Badge key={p.node_id} tone={p.link_up ? "green" : "red"}>{NODE_META[p.node_id as NodeId].label}</Badge>)}</div>
                </td>
                <td className="px-2 py-2 text-xs">{fmtTime(n.last_sync_at)}</td>
                <td className="px-2 py-2">
                  {field && coord ? (
                    <div className="flex flex-wrap gap-1">
                      {n.sim.offline
                        ? <Button small disabled={busy} onClick={() => act(() => put(id, "/api/sim", { offline: false }), "Back online")}>Go online</Button>
                        : <Button small disabled={busy} onClick={() => act(() => put(id, "/api/sim", { offline: true }), "Node offline")}>Go offline</Button>}
                      <Button small disabled={busy} onClick={() => act(() => put(id, "/api/sim", { blocked_peers: ALL_NODES.filter((x) => x !== id) }), "Partitioned from everyone")}>Partition</Button>
                      <Button small disabled={busy} onClick={() => act(async () => {
                        await put(id, "/api/sim", { blocked_peers: [], offline: false });
                        for (const other of FIELD_NODES) if (other !== id) {
                          const s: any = nodes?.[other]?.sim;
                          if (s?.blocked_peers?.includes(id)) await put(other, "/api/sim", { blocked_peers: s.blocked_peers.filter((x: string) => x !== id) });
                        }
                        await post(id, "/api/sync/run", { peer_id: "hub" }).catch(() => undefined);
                      }, "Reconnected")}>Reconnect</Button>
                    </div>
                  ) : <span className="text-xs text-slate-400">{field ? "coordinator only" : "-"}</span>}
                </td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </div>
  );
}
