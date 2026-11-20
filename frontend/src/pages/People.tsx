import { useState } from "react";
import { History, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { get, NODE_META, post, sessionFor } from "../api";
import { Badge, Button, Empty, Field, HistoryModal, Input, Modal, NodeChip, Select, STATUS_TONE, Textarea, useAction, useApp, usePoll } from "../ui";

const STATUSES = ["missing", "located", "found", "reunited", "needs_assistance", "unknown"];

function Verification({ v }: { v: string }) {
  if (v === "verified") return <Badge tone="green"><ShieldCheck size={12} /> verified</Badge>;
  if (v === "disputed") return <Badge tone="red"><ShieldAlert size={12} /> disputed - conflicting reports</Badge>;
  return <Badge tone="amber">unverified report</Badge>;
}

export default function People() {
  const { node, tick, role } = useApp();
  const { run } = useAction();
  const allowed = role === "coordinator" || role === "responder";
  const { data, error } = usePoll(() => (allowed ? get<any[]>(node, "/api/people") : Promise.resolve([])), [node, tick, role], 5000);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<any>({ name: "", age: "", description: "", last_known_location: "", contact: "", status: "missing", reporting_team: "" });
  const [upd, setUpd] = useState<any>();
  const [history, setHistory] = useState<any>();

  if (!allowed) {
    return (
      <div className="rounded-xl bg-white p-8 text-center ring-1 ring-slate-200">
        <Lock className="mx-auto text-slate-400" />
        <p className="mt-2 font-medium">Protected records</p>
        <p className="text-sm text-slate-500">The {role || "current"} role cannot access people-related records on {NODE_META[node].label}.</p>
      </div>
    );
  }
  const canDecrypt = role === "coordinator" && sessionFor(node)?.can_decrypt;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">People - missing & found (protected)</h1>
          <p className="max-w-3xl text-sm text-slate-500">
            Names, descriptions, locations, contacts and evidence are encrypted (AES-256-GCM) before they enter an event, so they are encrypted on disk,
            in transit and on the central server, which holds no key. Status updates are critical: a "found" report is never overwritten by a concurrent "missing" one;
            both are kept and the record is shown as disputed until a coordinator resolves it.
          </p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)}>Report person</Button>
      </div>
      <div className={`rounded-lg px-3 py-2 text-sm ring-1 ${canDecrypt ? "bg-sky-50 text-sky-900 ring-sky-200" : "bg-slate-50 text-slate-700 ring-slate-200"}`}>
        {canDecrypt ? "Coordinator session: details are decrypted in this node's memory for display. Each view is written to the audit log." : "Responder session: details are redacted. You can report people and update status, but only coordinators can read the details."}
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!data?.length ? <Empty>No person records.</Empty> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {data.map((p) => (
            <div key={p.person_id} className={`rounded-xl border bg-white p-4 shadow-sm ${p.verification === "disputed" ? "border-red-300" : "border-slate-200"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={STATUS_TONE[p.status]}>{p.status.replace("_", " ")}</Badge>
                <Verification v={p.verification} />
                <span className="mono text-xs text-slate-500">{p.person_id}</span>
                <span className="ml-auto"><NodeChip node={p.origin} small /></span>
              </div>
              {p.details ? (
                <dl className="mt-3 grid grid-cols-3 gap-x-3 gap-y-1 text-sm">
                  {Object.entries(p.details).map(([k, v]: any) => (
                    <div key={k} className="contents"><dt className="text-slate-500">{k.replace(/_/g, " ")}</dt><dd className="col-span-2 text-slate-900">{v}</dd></div>
                  ))}
                </dl>
              ) : <p className="mt-3 text-sm italic text-slate-500"><Lock size={12} className="inline" /> details redacted for this role</p>}
              {p.evidence?.length > 0 && (
                <ul className="mt-2 space-y-1 border-l-2 border-slate-200 pl-3 text-sm">
                  {p.evidence.map((e: any) => <li key={e.note_id}><NodeChip node={e.origin} small /> {e.text}</li>)}
                </ul>
              )}
              {!p.evidence && p.evidence_count > 0 && <p className="mt-1 text-xs text-slate-500">{p.evidence_count} encrypted evidence note(s)</p>}
              <div className="mt-2 text-xs text-slate-500">Reported by {p.reporting_team || "unspecified team"} · updated {new Date(p.updated_at).toLocaleString()}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button small disabled={p.verification === "disputed"} onClick={() => setUpd({ p, status: p.status, evidence: "" })}>Update status</Button>
                {role === "coordinator" && p.verification === "unverified" && <Button small onClick={() => run(() => post(node, `/api/people/${p.person_id}/verify`), "Current status verified")}>Verify current status</Button>}
                {p.verification === "disputed" && <a href="#conflicts" className="text-xs font-medium text-red-700 underline">Resolve in Conflict Center</a>}
                <Button small kind="ghost" onClick={() => setHistory(p)}><History size={13} /> History</Button>
              </div>
            </div>
          ))}
        </div>
      )}
      {creating && (
        <Modal title="Report a person" onClose={() => setCreating(false)}>
          <p className="mb-3 text-xs text-slate-500">Collect only what is needed to reunite or assist. Every field except status is encrypted.</p>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); run(() => post(node, "/api/people", form), "Encrypted record saved locally").then((r) => r && setCreating(false)); }}>
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Age"><Input value={form.age} onChange={(e) => setForm({ ...form, age: e.target.value })} /></Field>
            <Field label="Status"><Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} options={STATUSES} /></Field>
            <Field label="Reporting team"><Input value={form.reporting_team} onChange={(e) => setForm({ ...form, reporting_team: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Last known location"><Input value={form.last_known_location} onChange={(e) => setForm({ ...form, last_known_location: e.target.value })} /></Field></div>
            <div className="col-span-2"><Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
            <div className="col-span-2"><Field label="Contact (family / reporter)"><Input value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} /></Field></div>
            <div className="col-span-2 flex justify-end gap-2"><Button onClick={() => setCreating(false)}>Cancel</Button><Button kind="primary" type="submit">Save encrypted</Button></div>
          </form>
        </Modal>
      )}
      {upd && (
        <Modal title="Update status" onClose={() => setUpd(undefined)}>
          <div className="grid gap-3">
            <Field label="New status"><Select value={upd.status} onChange={(e) => setUpd({ ...upd, status: e.target.value })} options={STATUSES} /></Field>
            <Field label="Evidence (encrypted)"><Textarea value={upd.evidence} onChange={(e) => setUpd({ ...upd, evidence: e.target.value })} placeholder="Who saw them, where, when" /></Field>
            <p className="text-xs text-slate-500">The new status is shown as an unverified report until a coordinator verifies it.</p>
            <div className="flex justify-end gap-2"><Button onClick={() => setUpd(undefined)}>Cancel</Button>
              <Button kind="primary" onClick={() => run(() => post(node, `/api/people/${upd.p.person_id}/status`, { status: upd.status, evidence: upd.evidence || null }), "Status update saved locally").then((r) => r && setUpd(undefined))}>Save</Button></div>
          </div>
        </Modal>
      )}
      {history && <HistoryModal entityType="person" entityId={history.person_id} title={`record ${history.person_id}`} onClose={() => setHistory(undefined)} />}
    </div>
  );
}
