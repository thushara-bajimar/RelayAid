import { useState } from "react";
import { History, KeyRound, Lock, Plus } from "lucide-react";
import { FIELD_NODES, get, NODE_META, post } from "../api";
import {
  Badge, Button, Card, Empty, Field, HistoryModal, Input, Modal, NodeChip, Select, STATUS_TONE, useAction, useApp, usePoll,
} from "../ui";

function Bar({ s }: { s: any }) {
  // stacked accounting bar: available | reserved | distributed | unavailable (2px surface gaps between segments)
  const parts = [
    { k: "available", v: s.available, c: "#1baf7a" },
    { k: "reserved", v: s.reserved, c: "#eda100" },
    { k: "distributed", v: s.distributed, c: "#2a78d6" },
    { k: "unavailable", v: s.unavailable, c: "#94a3b8" },
  ];
  const total = Math.max(1, parts.reduce((a, p) => a + p.v, 0));
  return (
    <div>
      <div className="flex h-2.5 gap-[2px] overflow-hidden rounded-full bg-slate-100">
        {parts.filter((p) => p.v > 0).map((p) => (
          <div key={p.k} title={`${p.k}: ${p.v}`} style={{ width: `${(p.v / total) * 100}%`, background: p.c }} />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-600">
        {parts.map((p) => (
          <span key={p.k} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: p.c }} />{p.k} <b className="tabular-nums text-slate-800">{p.v}</b></span>
        ))}
      </div>
    </div>
  );
}

export default function Supplies() {
  const { node, tick } = useApp();
  const { run } = useAction();
  const { data, error } = usePoll(() => get<any[]>(node, "/api/supplies"), [node, tick]);
  const { data: needs } = usePoll(() => get<any[]>(node, "/api/needs"), [node, tick], 10000);
  const { data: tasks } = usePoll(() => get<any[]>(node, "/api/tasks"), [node, tick], 10000);
  const { data: verify } = usePoll(() => get<any>(node, "/api/allocations/verify"), [node, tick], 5000);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<any>({ name: "", category: "general", kind: "quantity", quantity: 100, unit: "units", location: "", condition: "good", supply_id: "" });
  const [dialog, setDialog] = useState<{ type: string; s: any } | undefined>();
  const [d, setD] = useState<any>({});
  const [history, setHistory] = useState<any>();
  const [blocked, setBlocked] = useState<string>();

  const open = (type: string, s: any) => {
    setD({ amount: 1, to_node: FIELD_NODES.find((n) => n !== node), target: "none:", reason: "" });
    setBlocked(undefined);
    setDialog({ type, s });
  };
  const allocate = async () => {
    const [target_type, target_id] = d.target.split(":");
    try {
      await post(node, `/api/supplies/${dialog!.s.supply_id}/allocate`, { amount: Number(d.amount), target_type, target_id: target_id || null, note: d.reason });
      setDialog(undefined);
      run(async () => undefined, "Allocated locally");
    } catch (e: any) {
      setBlocked(e.message); // keep the dialog open and explain the safety rule
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Supplies & allocation rights</h1>
          <p className="max-w-3xl text-sm text-slate-500">
            Divisible stock is split into <b>escrow rights</b> held by each node; a node may allocate offline only up to the rights it holds.
            Indivisible resources (boats, ambulances) have a single <b>exclusive token</b>; only the holder can allocate. Merging alone cannot
            prevent double allocation, so a node without rights is refused even when that means waiting - safety over availability.
          </p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)}><Plus size={15} /> Register supply</Button>
      </div>
      {verify && (
        <div className={`rounded-lg px-3 py-2 text-sm ring-1 ${verify.ok ? "bg-emerald-50 text-emerald-900 ring-emerald-200" : "bg-red-50 text-red-900 ring-red-200"}`}>
          Allocation audit on {NODE_META[node].label}: {verify.ok ? "no double allocation, all quantities balance" : verify.problems.join("; ")} · {verify.supplies_checked} resources checked · {verify.invalid_allocation_events_rejected} invalid allocation events rejected by validation
        </div>
      )}
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!data?.length ? <Empty>No supplies registered.</Empty> : (
        <div className="grid gap-3 xl:grid-cols-2">
          {data.map((s) => {
            const mine = s.kind === "indivisible" ? s.holder === node : s.my_rights > 0;
            const active = s.allocations.filter((a: any) => a.status === "reserved");
            const pending = s.requests.filter((r: any) => r.status === "pending");
            return (
              <Card key={s.supply_id}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2 font-semibold text-slate-900">{s.name} <span className="mono text-xs font-normal text-slate-500">{s.supply_id}</span></div>
                    <div className="text-xs text-slate-500">{s.category} · {s.location || "no location"} · condition {s.condition}</div>
                  </div>
                  <div className="flex gap-1.5">
                    <Badge tone={s.kind === "indivisible" ? "violet" : "blue"}>{s.kind}</Badge>
                    <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge>
                  </div>
                </div>
                {s.kind === "quantity" ? (
                  <div className="mt-3 space-y-3">
                    <div className="text-sm"><b className="tabular-nums">{s.total}</b> {s.unit} total</div>
                    <Bar s={s} />
                    <div>
                      <div className="mb-1 text-xs font-medium text-slate-500">Allocation rights (unallocated, per node)</div>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(s.rights).map(([n, b]: any) => (
                          <span key={n} className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs ring-1 ${n === node ? "bg-slate-900 text-white ring-slate-900" : "bg-white ring-slate-200"}`}>
                            <span className="h-2 w-2 rounded-full" style={{ background: NODE_META[n as "alpha"]?.color }} />{NODE_META[n as "alpha"]?.label || n} <b className="tabular-nums">{b}</b>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className={`mt-3 flex items-center gap-3 rounded-lg p-3 ring-1 ${mine ? "bg-emerald-50 ring-emerald-200" : "bg-slate-50 ring-slate-200"}`}>
                    {mine ? <KeyRound className="text-emerald-700" size={20} /> : <Lock className="text-slate-500" size={20} />}
                    <div className="text-sm">
                      Exclusive allocation token held by <NodeChip node={s.holder} small /> <span className="mono text-xs text-slate-500">epoch {s.epoch}</span>
                      <div className="text-xs text-slate-600">{mine ? "This node may allocate or hand over this resource." : "This node cannot allocate it, online or offline, until the holder transfers the token."}</div>
                    </div>
                  </div>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button small kind="primary" onClick={() => open("allocate", s)}>Allocate</Button>
                  <Button small disabled={!mine} onClick={() => open("transfer", s)}>{s.kind === "indivisible" ? "Transfer token" : "Transfer rights"}</Button>
                  {!mine && <Button small onClick={() => open("request", s)}>Request rights</Button>}
                  {s.kind === "quantity" && <Button small onClick={() => open("restock", s)}>Restock</Button>}
                  {s.kind === "quantity" && <Button small disabled={!mine} onClick={() => open("write-off", s)}>Write off</Button>}
                  {s.kind === "indivisible" && mine && !active.length && (
                    <Button small onClick={() => run(() => post(node, `/api/supplies/${s.supply_id}/availability`, { available: s.status !== "available", reason: "" }), "Availability changed")}>
                      Mark {s.status === "available" ? "out of service" : "in service"}
                    </Button>
                  )}
                  <Button small kind="ghost" onClick={() => setHistory(s)}><History size={13} /> History</Button>
                </div>
                {pending.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    <div className="text-xs font-medium text-slate-500">Pending rights requests</div>
                    {pending.map((r: any) => (
                      <div key={r.request_id} className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-50 px-2 py-1.5 text-sm ring-1 ring-amber-200">
                        <NodeChip node={r.requested_by} small /> asks for {r.amount} {r.reason && <span className="text-slate-600">· {r.reason}</span>}
                        {mine && r.requested_by !== node && (
                          <span className="ml-auto flex gap-1">
                            <Button small onClick={() => run(() => post(node, `/api/supplies/${s.supply_id}/transfer`, { to_node: r.requested_by, amount: r.amount, request_id: r.request_id }), "Rights transferred")}>Approve</Button>
                            <Button small kind="ghost" onClick={() => run(() => post(node, `/api/supplies/${s.supply_id}/requests/${r.request_id}/decline`, { reason: "" }), "Declined")}>Decline</Button>
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {s.allocations.length > 0 && (
                  <div className="mt-3">
                    <div className="mb-1 text-xs font-medium text-slate-500">Allocations</div>
                    <ul className="space-y-1 text-sm">
                      {[...s.allocations].reverse().slice(0, 6).map((a: any) => (
                        <li key={a.allocation_id} className="flex flex-wrap items-center gap-2">
                          <NodeChip node={a.node_id} small /> <span className="tabular-nums">{a.amount}</span>
                          <Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>
                          <span className="truncate text-xs text-slate-500">{a.target_type !== "none" ? `${a.target_type} ${a.target_id?.slice(-6)}` : ""} {a.note}</span>
                          {a.status === "reserved" && a.node_id === node && (
                            <span className="ml-auto flex gap-1">
                              {s.kind === "quantity" && <Button small onClick={() => run(() => post(node, `/api/allocations/${a.allocation_id}/distribute`), "Marked distributed")}>Distributed</Button>}
                              <Button small kind="ghost" onClick={() => run(() => post(node, `/api/allocations/${a.allocation_id}/release`), "Released")}>Release</Button>
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {s.transfers.length > 0 && (
                  <div className="mt-2 text-xs text-slate-500">
                    Transfers: {s.transfers.map((t: any) => `${NODE_META[t.from_node as "alpha"]?.label}→${NODE_META[t.to_node as "alpha"]?.label} (${t.amount})`).join(", ")}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {dialog && (
        <Modal title={`${dialog.type.replace("-", " ")}: ${dialog.s.name}`} onClose={() => setDialog(undefined)}>
          <div className="space-y-3">
            {dialog.type === "allocate" && (
              <>
                <p className="text-sm text-slate-600">
                  {dialog.s.kind === "indivisible"
                    ? <>Requires the exclusive token. Holder: <NodeChip node={dialog.s.holder} small /></>
                    : <>{NODE_META[node].label} holds <b>{dialog.s.my_rights}</b> {dialog.s.unit} of rights.</>}
                </p>
                {dialog.s.kind === "quantity" && <Field label="Amount"><Input type="number" min={1} value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} /></Field>}
                <Field label="For">
                  <Select value={d.target} onChange={(e) => setD({ ...d, target: e.target.value })}
                    options={[["none:", "General use"], ...(needs || []).filter((n) => n.status !== "fulfilled").map((n) => [`need:${n.need_id}`, `Need: ${n.description.slice(0, 50)}`] as [string, string]), ...(tasks || []).map((t) => [`task:${t.task_id}`, `Task: ${t.title.slice(0, 50)}`] as [string, string])]} />
                </Field>
                <Field label="Note"><Input value={d.reason} onChange={(e) => setD({ ...d, reason: e.target.value })} /></Field>
                {blocked && (
                  <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-900 ring-1 ring-red-200">
                    <b>Allocation blocked.</b> {blocked}
                    <div className="mt-1 text-xs text-red-800">Ordinary coordination continues; only this scarce-resource allocation waits for rights.</div>
                  </div>
                )}
                <div className="flex justify-end gap-2"><Button onClick={() => setDialog(undefined)}>Close</Button><Button kind="primary" onClick={allocate}>Allocate</Button></div>
              </>
            )}
            {dialog.type === "transfer" && (
              <>
                <p className="text-sm text-slate-600">The transfer is recorded as an event. {NODE_META[node].label} loses the rights immediately; the receiver can use them once the event reaches it.</p>
                <Field label="To node"><Select value={d.to_node} onChange={(e) => setD({ ...d, to_node: e.target.value })} options={FIELD_NODES.filter((n) => n !== node).map((n) => [n, NODE_META[n].label] as [string, string])} /></Field>
                {dialog.s.kind === "quantity" && <Field label={`Amount (you hold ${dialog.s.my_rights})`}><Input type="number" min={1} value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} /></Field>}
                <div className="flex justify-end gap-2"><Button onClick={() => setDialog(undefined)}>Cancel</Button>
                  <Button kind="primary" onClick={() => run(() => post(node, `/api/supplies/${dialog.s.supply_id}/transfer`, { to_node: d.to_node, amount: Number(d.amount) }), "Transfer recorded").then((r) => r && setDialog(undefined))}>Transfer</Button></div>
              </>
            )}
            {dialog.type === "request" && (
              <>
                <Field label="Amount"><Input type="number" min={1} value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} /></Field>
                <Field label="Reason"><Input value={d.reason} onChange={(e) => setD({ ...d, reason: e.target.value })} /></Field>
                <div className="flex justify-end gap-2"><Button onClick={() => setDialog(undefined)}>Cancel</Button>
                  <Button kind="primary" onClick={() => run(() => post(node, `/api/supplies/${dialog.s.supply_id}/request-rights`, { amount: Number(d.amount), reason: d.reason }), "Request recorded; it reaches the holder on next sync").then((r) => r && setDialog(undefined))}>Request</Button></div>
              </>
            )}
            {(dialog.type === "restock" || dialog.type === "write-off") && (
              <>
                <p className="text-sm text-slate-600">{dialog.type === "restock" ? "New stock received at this node becomes rights held by this node." : "Damaged or lost stock is removed from this node's rights."}</p>
                <Field label="Amount"><Input type="number" min={1} value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} /></Field>
                <Field label="Note"><Input value={d.reason} onChange={(e) => setD({ ...d, reason: e.target.value })} /></Field>
                <div className="flex justify-end gap-2"><Button onClick={() => setDialog(undefined)}>Cancel</Button>
                  <Button kind="primary" onClick={() => run(() => post(node, `/api/supplies/${dialog.s.supply_id}/${dialog.type}`, { amount: Number(d.amount), note: d.reason }), "Saved locally").then((r) => r && setDialog(undefined))}>Save</Button></div>
              </>
            )}
          </div>
        </Modal>
      )}

      {creating && (
        <Modal title="Register supply" onClose={() => setCreating(false)}>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => {
            e.preventDefault();
            const { supply_id, ...body } = form;
            run(() => post(node, `/api/supplies${supply_id ? `?supply_id=${encodeURIComponent(supply_id)}` : ""}`, { ...body, quantity: Number(body.quantity) }), "Supply registered; this node holds all initial rights").then((r) => r && setCreating(false));
          }}>
            <div className="col-span-2"><Field label="Name"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field></div>
            <Field label="Kind"><Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} options={[["quantity", "Quantity (divisible)"], ["indivisible", "Indivisible unit"]]} /></Field>
            <Field label="Resource ID" hint="optional, e.g. R-01"><Input value={form.supply_id} onChange={(e) => setForm({ ...form, supply_id: e.target.value })} /></Field>
            {form.kind === "quantity" && <Field label="Quantity"><Input type="number" min={1} value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>}
            <Field label="Unit"><Input value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} /></Field>
            <Field label="Category"><Input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} /></Field>
            <Field label="Condition"><Select value={form.condition} onChange={(e) => setForm({ ...form, condition: e.target.value })} options={["good", "fair", "damaged"]} /></Field>
            <div className="col-span-2"><Field label="Location"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field></div>
            <div className="col-span-2 flex justify-end gap-2"><Button onClick={() => setCreating(false)}>Cancel</Button><Button kind="primary" type="submit">Register</Button></div>
          </form>
        </Modal>
      )}
      {history && <HistoryModal entityType="supply" entityId={history.supply_id} title={history.name} onClose={() => setHistory(undefined)} />}
    </div>
  );
}
