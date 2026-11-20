import { useState } from "react";
import { get, post } from "../api";
import { Badge, Button, Card, Empty, Field, Input, NodeChip, Select, Textarea, useAction, useApp, usePoll } from "../ui";

const POLICY_TEXT: Record<string, string> = {
  critical: "Critical field: not auto-resolved. Both values are kept until a coordinator decides.",
  lww: "Last-writer-wins on (Lamport clock, node id): merged automatically and deterministically, recorded for transparency.",
  lattice: "Lattice merge: the more advanced value wins (e.g. in-progress beats assigned; higher priority wins).",
  invariant: "Invariant violation detected after merging independent updates (e.g. occupancy above capacity).",
};

function fmt(v: any) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k} ${x}`).join(", ");
  return String(v).replace(/_/g, " ");
}

function ConflictCard({ c }: { c: any }) {
  const { node, role } = useApp();
  const { run } = useAction();
  const values = Array.from(new Set(c.candidates.map((k: any) => JSON.stringify(k.value)))).map((v) => JSON.parse(v as string));
  const [choice, setChoice] = useState(JSON.stringify(values[0]));
  const [custom, setCustom] = useState("");
  const [why, setWhy] = useState("");
  const isOpen = c.status === "open";
  const coord = role === "coordinator";
  const tone = { open: "red", resolved: "green", auto_merged: "blue", acknowledged: "amber" }[c.status as string] || "gray";
  const cands = c.kind === "invariant" ? c.candidates.slice(-4) : c.candidates;

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{c.status.replace("_", " ")}</Badge>
        <Badge>{c.policy}</Badge>
        <span className="font-semibold text-slate-900">{c.entity_label}</span>
        <span className="text-sm text-slate-500">· {c.entity_type}.{c.field}</span>
        <span className="mono ml-auto text-xs text-slate-400">{c.conflict_id}</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{POLICY_TEXT[c.policy]}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {cands.map((k: any) => (
          <div key={k.event_id} className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
            <div className="flex items-center gap-2 text-xs"><NodeChip node={k.origin} small /> <span className="mono text-slate-500">L{k.lamport}</span><span className="ml-auto text-slate-500">{new Date(k.created_at).toLocaleString()}</span></div>
            <div className="mt-1 text-base font-semibold text-slate-900">{fmt(k.value)}</div>
            <div className="text-[11px] text-slate-500">{k.event_type}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 text-sm text-slate-700">
        {c.kind === "invariant" ? "Current" : isOpen ? "Provisional display value" : "Current value"}: <b>{fmt(c.current_value)}</b>
        {c.resolution_kind && <span className="text-slate-500"> · resolved by {c.resolution_kind.replace("_", " ")}</span>}
        {c.rationale && <div className="mt-1 text-xs text-slate-600">Rationale: “{c.rationale}”</div>}
      </div>
      {isOpen && c.kind === "concurrent_write" && (
        coord ? (
          <div className="mt-3 grid gap-2 border-t border-slate-100 pt-3 sm:grid-cols-2">
            <Field label="Resolve to">
              <Select value={choice} onChange={(e) => setChoice(e.target.value)} options={[...values.map((v) => [JSON.stringify(v), fmt(v)] as [string, string]), ["__custom", "Another value…"]]} />
            </Field>
            {choice === "__custom" && <Field label="Value"><Input value={custom} onChange={(e) => setCustom(e.target.value)} /></Field>}
            <div className="sm:col-span-2"><Field label="Rationale (recorded in the resolution event)"><Textarea value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. Verified by phone with camp B volunteer" /></Field></div>
            <div><Button kind="primary" disabled={!why} onClick={() => {
              let value: any = choice === "__custom" ? custom : JSON.parse(choice);
              if (choice === "__custom" && /^\d+$/.test(custom)) value = Number(custom);
              run(() => post(node, `/api/conflicts/${c.conflict_id}/resolve`, { value, rationale: why }), "Resolution recorded; it reaches other nodes on sync");
            }}>Record resolution</Button></div>
          </div>
        ) : <p className="mt-3 text-xs text-slate-500">A coordinator must resolve this conflict.</p>
      )}
      {isOpen && c.kind === "invariant" && coord && (
        <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3">
          <div className="min-w-60 flex-1"><Field label="Acknowledge with a note"><Input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. overflow moved to tent area" /></Field></div>
          <Button disabled={!why} onClick={() => run(() => post(node, `/api/conflicts/${c.conflict_id}/acknowledge`, { note: why }), "Acknowledged")}>Acknowledge</Button>
        </div>
      )}
    </Card>
  );
}

export default function Conflicts() {
  const { node, tick } = useApp();
  const { data, error } = usePoll(() => get<any[]>(node, "/api/conflicts"), [node, tick]);
  const [tab, setTab] = useState("open");
  const counts = (data || []).reduce((a: any, c) => ({ ...a, [c.status]: (a[c.status] || 0) + 1 }), {});
  const shown = (data || []).filter((c) => (tab === "open" ? c.status === "open" || c.status === "acknowledged" : c.status === tab));
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Conflict center</h1>
        <p className="max-w-3xl text-sm text-slate-500">Conflicts are derived from the event log on every node, so all replicas with the same events show the same conflicts. Nothing here is hidden because a device synchronized later: an open conflict closes only through a resolution event that has seen every competing update.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {[["open", "Needs attention"], ["resolved", "Resolved"], ["auto_merged", "Auto-merged"]].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`rounded-lg px-3 py-1.5 text-sm ring-1 ${tab === k ? "bg-slate-900 text-white ring-slate-900" : "bg-white ring-slate-200"}`}>
            {l} <span className="tabular-nums opacity-70">{k === "open" ? (counts.open || 0) + (counts.acknowledged || 0) : counts[k] || 0}</span>
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!shown.length ? <Empty>{tab === "open" ? "No unresolved conflicts on this node." : "Nothing here yet."}</Empty> : (
        <div className="grid gap-3 xl:grid-cols-2">{shown.map((c) => <ConflictCard key={c.conflict_id} c={c} />)}</div>
      )}
    </div>
  );
}
