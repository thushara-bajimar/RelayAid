import { useState } from "react";
import { History, Plus } from "lucide-react";
import { get, post } from "../api";
import {
  Badge, Button, Card, DisputedBadge, Empty, Field, HistoryModal, Input, Modal, NodeChip, PRIORITY_TONE, Select, Textarea,
  useAction, useApp, usePoll,
} from "../ui";

const COLUMNS = ["open", "assigned", "in_progress", "completed", "cancelled"];

export default function Tasks() {
  const { node, tick } = useApp();
  const { run } = useAction();
  const { data: tasks, error } = usePoll(() => get<any[]>(node, "/api/tasks"), [node, tick]);
  const { data: teams } = usePoll(() => get<any[]>(node, "/api/teams"), [node, tick]);
  const { data: needs } = usePoll(() => get<any[]>(node, "/api/needs"), [node, tick], 10000);
  const [creating, setCreating] = useState(false);
  const [teamForm, setTeamForm] = useState<any>();
  const [form, setForm] = useState<any>({ title: "", description: "", priority: "medium", location: "", need_id: "" });
  const [history, setHistory] = useState<any>();
  const teamName = (id?: string) => teams?.find((t) => t.team_id === id)?.name || (id ? id.slice(-6) : "unassigned");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Tasks & teams</h1>
          <p className="max-w-3xl text-sm text-slate-500">Task status is a lattice (open → assigned → in progress → completed): concurrent progress merges forward automatically. Team assignment is critical: two teams assigned concurrently is flagged, never silently replaced.</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setTeamForm({ name: "", specialty: "", members: 4 })}>Register team</Button>
          <Button kind="primary" onClick={() => setCreating(true)}><Plus size={15} /> New task</Button>
        </div>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {COLUMNS.map((col) => {
          const items = (tasks || []).filter((t) => t.status === col);
          return (
            <div key={col} className="rounded-xl bg-slate-100/70 p-2">
              <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{col.replace("_", " ")} <span>{items.length}</span></div>
              <div className="space-y-2">
                {items.map((t) => (
                  <div key={t.task_id} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                    <div className="flex items-center gap-1.5"><Badge tone={PRIORITY_TONE[t.priority]}>{t.priority}</Badge><span className="ml-auto"><NodeChip node={t.origin} small /></span></div>
                    <div className="mt-1.5 text-sm font-medium text-slate-900">{t.title}</div>
                    {t.location && <div className="text-xs text-slate-500">{t.location}</div>}
                    <div className="mt-1 text-xs text-slate-600">Team: <b>{teamName(t.assigned_team)}</b></div>
                    <div className="mt-1"><DisputedBadge fields={t.disputed_fields} /></div>
                    {t.notes.length > 0 && <div className="mt-1 text-xs text-slate-500">{t.notes.length} note(s)</div>}
                    <div className="mt-2 grid gap-1.5">
                      <Select aria-label="Assign team" value={t.assigned_team || ""} disabled={t.disputed_fields.includes("assigned_team")}
                        onChange={(e) => run(() => post(node, `/api/tasks/${t.task_id}/assign`, { team_id: e.target.value || null }), "Assignment saved locally")}
                        options={[["", "Unassigned"], ...(teams || []).map((x) => [x.team_id, x.name] as [string, string])]} />
                      <Select aria-label="Status" value={t.status} disabled={t.disputed_fields.includes("status")}
                        onChange={(e) => run(() => post(node, `/api/tasks/${t.task_id}/status`, { status: e.target.value, note: "" }), "Status saved locally")}
                        options={COLUMNS} />
                      <Button small kind="ghost" onClick={() => setHistory(t)}><History size={13} /> History</Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <Card title="Teams" subtitle="Registered response teams known to this node">
        {!teams?.length ? <Empty>No teams yet.</Empty> : (
          <div className="flex flex-wrap gap-2">
            {teams.map((t) => (
              <div key={t.team_id} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
                <div className="font-medium">{t.name}</div>
                <div className="text-xs text-slate-500">{t.specialty} · {t.members} members · home <NodeChip node={t.home_node} small /></div>
              </div>
            ))}
          </div>
        )}
      </Card>
      {creating && (
        <Modal title="New task" onClose={() => setCreating(false)}>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); run(() => post(node, "/api/tasks", { ...form, need_id: form.need_id || null }), "Task created locally").then((r) => r && setCreating(false)); }}>
            <div className="col-span-2"><Field label="Title"><Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field></div>
            <div className="col-span-2"><Field label="Description"><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
            <Field label="Priority"><Select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} options={["low", "medium", "high", "critical"]} /></Field>
            <Field label="Location"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Linked need"><Select value={form.need_id} onChange={(e) => setForm({ ...form, need_id: e.target.value })} options={[["", "-"], ...(needs || []).map((n) => [n.need_id, n.description.slice(0, 60)] as [string, string])]} /></Field></div>
            <div className="col-span-2 flex justify-end gap-2"><Button onClick={() => setCreating(false)}>Cancel</Button><Button kind="primary" type="submit">Create</Button></div>
          </form>
        </Modal>
      )}
      {teamForm && (
        <Modal title="Register team" onClose={() => setTeamForm(undefined)}>
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); run(() => post(node, "/api/teams", { ...teamForm, members: Number(teamForm.members) }), "Team registered").then((r) => r && setTeamForm(undefined)); }}>
            <Field label="Name"><Input required value={teamForm.name} onChange={(e) => setTeamForm({ ...teamForm, name: e.target.value })} /></Field>
            <Field label="Specialty"><Input value={teamForm.specialty} onChange={(e) => setTeamForm({ ...teamForm, specialty: e.target.value })} /></Field>
            <Field label="Members"><Input type="number" min={0} value={teamForm.members} onChange={(e) => setTeamForm({ ...teamForm, members: e.target.value })} /></Field>
            <div className="flex justify-end gap-2"><Button onClick={() => setTeamForm(undefined)}>Cancel</Button><Button kind="primary" type="submit">Register</Button></div>
          </form>
        </Modal>
      )}
      {history && <HistoryModal entityType="task" entityId={history.task_id} title={history.title} onClose={() => setHistory(undefined)} />}
    </div>
  );
}
