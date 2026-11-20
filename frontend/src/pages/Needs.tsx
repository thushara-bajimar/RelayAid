import { useState } from "react";
import { History, Plus } from "lucide-react";
import { get, patch, post } from "../api";
import {
  Badge, Button, Card, DisputedBadge, Empty, Field, HistoryModal, Input, Modal, NodeChip, PRIORITY_TONE, Select,
  STATUS_TONE, Textarea, useAction, useApp, usePoll,
} from "../ui";

const CATEGORIES = ["water", "food", "medical", "shelter", "evacuation", "rescue", "sanitation", "other"];
const PRIORITIES = ["low", "medium", "high", "critical"];
const STATUSES = ["open", "in_progress", "fulfilled", "cancelled"];

function NeedForm({ initial, teams, onSubmit, onClose, title }: any) {
  const [f, setF] = useState<any>(initial);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal title={title} onClose={onClose}>
      <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); onSubmit({ ...f, quantity: Number(f.quantity), responsible_team: f.responsible_team || null }); }}>
        <Field label="Category"><Select value={f.category} onChange={set("category")} options={CATEGORIES} /></Field>
        <Field label="Priority"><Select value={f.priority} onChange={set("priority")} options={PRIORITIES} /></Field>
        <div className="col-span-2"><Field label="Description"><Textarea required value={f.description} onChange={set("description")} /></Field></div>
        <Field label="Quantity"><Input type="number" min={0} value={f.quantity} onChange={set("quantity")} /></Field>
        <Field label="Unit"><Input value={f.unit} onChange={set("unit")} /></Field>
        <Field label="Location"><Input value={f.location} onChange={set("location")} /></Field>
        <Field label="Responsible team"><Select value={f.responsible_team || ""} onChange={set("responsible_team")} options={[["", "-"], ...teams.map((t: any) => [t.team_id, t.name])]} /></Field>
        {"status" in f && <Field label="Status"><Select value={f.status} onChange={set("status")} options={STATUSES} /></Field>}
        <div className="col-span-2 flex justify-end gap-2 pt-2"><Button onClick={onClose}>Cancel</Button><Button kind="primary" type="submit">Save locally</Button></div>
      </form>
    </Modal>
  );
}

export default function Needs() {
  const { node, tick } = useApp();
  const { run } = useAction();
  const { data: needs, error } = usePoll(() => get<any[]>(node, "/api/needs"), [node, tick]);
  const { data: teams } = usePoll(() => get<any[]>(node, "/api/teams"), [node, tick], 10000);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<any>();
  const [history, setHistory] = useState<any>();
  const [note, setNote] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("active");
  const teamName = (id?: string) => teams?.find((t) => t.team_id === id)?.name;
  const shown = (needs || []).filter((n) => filter === "all" || (filter === "active" ? ["open", "in_progress"].includes(n.status) : n.status === filter));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Emergency needs</h1>
          <p className="text-sm text-slate-500">Recorded locally, synchronized later. Notes from every team are merged (grow-only set); priority merges to the highest concurrent value; status is a critical field.</p>
        </div>
        <div className="flex gap-2">
          <Select value={filter} onChange={(e) => setFilter(e.target.value)} options={[["active", "Active"], ["all", "All"], ...STATUSES.map((s) => [s, s.replace("_", " ")] as [string, string])]} />
          <Button kind="primary" onClick={() => setCreating(true)}><Plus size={15} /> New need</Button>
        </div>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!shown.length ? <Empty>No needs in this view.</Empty> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {shown.map((n) => (
            <Card key={n.need_id}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={PRIORITY_TONE[n.priority]}>{n.priority}</Badge>
                <Badge tone={STATUS_TONE[n.status]}>{n.status.replace("_", " ")}</Badge>
                <Badge>{n.category}</Badge>
                <DisputedBadge fields={n.disputed_fields} />
                <span className="ml-auto"><NodeChip node={n.origin} small /></span>
              </div>
              <p className="mt-2 font-medium text-slate-900">{n.description}</p>
              <div className="mt-1 text-sm text-slate-600">
                {n.quantity} {n.unit}{n.location && <> · {n.location}</>}{n.responsible_team && <> · team: {teamName(n.responsible_team) || n.responsible_team}</>}
              </div>
              {n.notes.length > 0 && (
                <ul className="mt-2 space-y-1 border-l-2 border-slate-200 pl-3 text-sm">
                  {n.notes.map((x: any) => <li key={x.note_id}><NodeChip node={x.origin} small /> <span className="text-slate-700">{x.text}</span></li>)}
                </ul>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Input placeholder="Add a note" value={note[n.need_id] || ""} onChange={(e) => setNote({ ...note, [n.need_id]: e.target.value })} />
                <Button small disabled={!note[n.need_id]} onClick={() => run(() => post(node, `/api/needs/${n.need_id}/notes`, { text: note[n.need_id] }), "Note saved locally").then(() => setNote({ ...note, [n.need_id]: "" }))}>Add note</Button>
                <Button small onClick={() => setEditing(n)}>Edit</Button>
                {n.status !== "fulfilled" && <Button small disabled={n.disputed_fields.includes("status")} onClick={() => run(() => patch(node, `/api/needs/${n.need_id}`, { status: "fulfilled" }), "Marked fulfilled")}>Mark fulfilled</Button>}
                <Button small kind="ghost" onClick={() => setHistory(n)}><History size={13} /> History</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {creating && <NeedForm title="Record a need" teams={teams || []} onClose={() => setCreating(false)}
        initial={{ category: "water", priority: "medium", description: "", quantity: 1, unit: "units", location: "", responsible_team: "" }}
        onSubmit={(f: any) => run(() => post(node, "/api/needs", f), "Need recorded locally").then((r) => r && setCreating(false))} />}
      {editing && <NeedForm title="Update need" teams={teams || []} onClose={() => setEditing(undefined)} initial={editing}
        onSubmit={(f: any) => {
          const changed: any = {};
          for (const k of ["category", "description", "quantity", "unit", "priority", "location", "status", "responsible_team"]) if (f[k] !== editing[k]) changed[k] = f[k];
          if (!Object.keys(changed).length) return setEditing(undefined);
          run(() => patch(node, `/api/needs/${editing.need_id}`, changed), "Update saved locally").then((r) => r && setEditing(undefined));
        }} />}
      {history && <HistoryModal entityType="need" entityId={history.need_id} title={history.description} onClose={() => setHistory(undefined)} />}
    </div>
  );
}
