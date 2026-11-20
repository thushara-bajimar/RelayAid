import { useState } from "react";
import { History, Minus, Plus } from "lucide-react";
import { get, patch, post } from "../api";
import {
  Badge, Button, Card, DisputedBadge, Empty, Field, HistoryModal, Input, Modal, NodeChip, Select, STATUS_TONE, Textarea,
  useAction, useApp, usePoll,
} from "../ui";

export default function Shelters() {
  const { node, tick, toast } = useApp();
  const { run } = useAction();
  const { data, error } = usePoll(() => get<any[]>(node, "/api/shelters"), [node, tick]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<any>();
  const [history, setHistory] = useState<any>();
  const [delta, setDelta] = useState<Record<string, string>>({});
  const [form, setForm] = useState<any>({ name: "", location: "", capacity: 100, accessibility: "", conditions: "", status: "open" });

  const change = (s: any, sign: number) => {
    const d = Number(delta[s.shelter_id] || 1) * sign;
    run(() => post(node, `/api/shelters/${s.shelter_id}/occupancy`, { delta: d, reason: sign > 0 ? "arrivals" : "departures" }))
      .then((r: any) => r?.over_capacity && toast("Recorded, but this shelter is now over capacity. The overflow is flagged in the Conflict Center.", "error"));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Shelters</h1>
          <p className="text-sm text-slate-500">Occupancy is a counter of arrivals and departures, so concurrent check-ins on different devices all count. Capacity and status are critical fields: concurrent disagreements are flagged, not overwritten. Over-capacity is surfaced as an invariant conflict.</p>
        </div>
        <Button kind="primary" onClick={() => setCreating(true)}><Plus size={15} /> Register shelter</Button>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!data?.length ? <Empty>No shelters registered.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data.map((s) => {
            const pct = s.capacity ? Math.min(100, (s.occupancy / s.capacity) * 100) : 100;
            const over = s.occupancy > s.capacity;
            return (
              <Card key={s.shelter_id}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold text-slate-900">{s.name}</div>
                    <div className="text-xs text-slate-500">{s.location}</div>
                  </div>
                  <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge>
                </div>
                <div className="mt-3 flex items-baseline justify-between text-sm">
                  <span className="tabular-nums"><span className={`text-2xl font-semibold ${over ? "text-red-700" : ""}`}>{s.occupancy}</span> / {s.capacity}</span>
                  <span className="text-xs text-slate-500">{s.available_space} spaces free</span>
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100" role="meter" aria-valuenow={s.occupancy} aria-valuemax={s.capacity} aria-label="occupancy">
                  <div className={`h-full rounded-full ${over ? "bg-red-600" : pct > 85 ? "bg-amber-500" : "bg-emerald-600"}`} style={{ width: `${pct}%` }} />
                </div>
                {over && <p className="mt-1 text-xs font-medium text-red-700">Over capacity by {s.occupancy - s.capacity}</p>}
                <div className="mt-2 flex flex-wrap gap-1"><DisputedBadge fields={s.disputed_fields} /></div>
                {s.accessibility && <p className="mt-2 text-xs text-slate-600"><b>Access:</b> {s.accessibility}</p>}
                {s.conditions && <p className="text-xs text-slate-600"><b>Conditions:</b> {s.conditions}</p>}
                <div className="mt-3 flex items-center gap-2">
                  <div className="w-20"><Input type="number" min={1} value={delta[s.shelter_id] || "1"} onChange={(e) => setDelta({ ...delta, [s.shelter_id]: e.target.value })} aria-label="people" /></div>
                  <Button small onClick={() => change(s, 1)}><Plus size={13} /> Check in</Button>
                  <Button small onClick={() => change(s, -1)}><Minus size={13} /> Check out</Button>
                </div>
                <div className="mt-2 flex gap-2">
                  <Button small onClick={() => setEditing({ ...s })}>Edit</Button>
                  <Button small kind="ghost" onClick={() => setHistory(s)}><History size={13} /> History</Button>
                </div>
                {s.updates.length > 0 && (
                  <ul className="mt-3 max-h-28 space-y-0.5 overflow-y-auto border-t border-slate-100 pt-2 text-xs text-slate-600">
                    {[...s.updates].reverse().slice(0, 6).map((u: any) => (
                      <li key={u.event_id}><NodeChip node={u.origin} small /> {u.kind === "occupancy" ? `${u.delta > 0 ? "+" : ""}${u.delta}` : "note"} {u.reason && `· ${u.reason}`}</li>
                    ))}
                  </ul>
                )}
              </Card>
            );
          })}
        </div>
      )}
      {creating && (
        <Modal title="Register shelter" onClose={() => setCreating(false)}>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); run(() => post(node, "/api/shelters", { ...form, capacity: Number(form.capacity) }), "Shelter registered locally").then((r) => r && setCreating(false)); }}>
            <div className="col-span-2"><Field label="Name"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field></div>
            <Field label="Location"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
            <Field label="Capacity"><Input type="number" min={0} value={form.capacity} onChange={(e) => setForm({ ...form, capacity: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Accessibility"><Input value={form.accessibility} onChange={(e) => setForm({ ...form, accessibility: e.target.value })} /></Field></div>
            <div className="col-span-2"><Field label="Conditions"><Textarea value={form.conditions} onChange={(e) => setForm({ ...form, conditions: e.target.value })} /></Field></div>
            <div className="col-span-2 flex justify-end gap-2"><Button onClick={() => setCreating(false)}>Cancel</Button><Button kind="primary" type="submit">Save</Button></div>
          </form>
        </Modal>
      )}
      {editing && (
        <Modal title={`Update ${editing.name}`} onClose={() => setEditing(undefined)}>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => {
            e.preventDefault();
            const orig = data!.find((x) => x.shelter_id === editing.shelter_id);
            const ch: any = {};
            for (const k of ["name", "location", "capacity", "accessibility", "conditions", "status"]) {
              const v = k === "capacity" ? Number(editing[k]) : editing[k];
              if (v !== orig[k]) ch[k] = v;
            }
            if (!Object.keys(ch).length) return setEditing(undefined);
            run(() => patch(node, `/api/shelters/${editing.shelter_id}`, ch), "Shelter updated locally").then((r) => r && setEditing(undefined));
          }}>
            <Field label="Capacity"><Input type="number" min={0} value={editing.capacity} onChange={(e) => setEditing({ ...editing, capacity: e.target.value })} /></Field>
            <Field label="Status"><Select value={editing.status} onChange={(e) => setEditing({ ...editing, status: e.target.value })} options={["open", "full", "closed"]} /></Field>
            <div className="col-span-2"><Field label="Accessibility"><Input value={editing.accessibility} onChange={(e) => setEditing({ ...editing, accessibility: e.target.value })} /></Field></div>
            <div className="col-span-2"><Field label="Conditions"><Textarea value={editing.conditions} onChange={(e) => setEditing({ ...editing, conditions: e.target.value })} /></Field></div>
            <div className="col-span-2 flex justify-end gap-2"><Button onClick={() => setEditing(undefined)}>Cancel</Button><Button kind="primary" type="submit">Save</Button></div>
          </form>
        </Modal>
      )}
      {history && <HistoryModal entityType="shelter" entityId={history.shelter_id} title={history.name} onClose={() => setHistory(undefined)} />}
    </div>
  );
}
