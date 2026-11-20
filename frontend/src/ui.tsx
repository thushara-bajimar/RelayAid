import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { get, NODE_META, NodeId } from "./api";

// ---------------------------------------------------------------- app context
export interface Ctx {
  node: NodeId;
  setNode: (n: NodeId) => void;
  toast: (msg: string, kind?: "ok" | "error" | "info") => void;
  role: string | undefined;
  tick: number; // bumps after any mutation so pages refetch
  refresh: () => void;
}
export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

// ---------------------------------------------------------------- polling hook
export function usePoll<T>(fn: () => Promise<T>, deps: unknown[], interval = 3000) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const load = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(undefined);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    load();
    if (!interval) return;
    const t = setInterval(load, interval);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, reload: load };
}

// ---------------------------------------------------------------- primitives
export function Card({ title, actions, children, className = "", subtitle }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; subtitle?: ReactNode;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-slate-800">{title}</h2>}
            {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  gray: "bg-slate-100 text-slate-700 ring-slate-200",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  red: "bg-red-50 text-red-800 ring-red-200",
  amber: "bg-amber-50 text-amber-900 ring-amber-200",
  blue: "bg-sky-50 text-sky-800 ring-sky-200",
  violet: "bg-violet-50 text-violet-800 ring-violet-200",
};
export function Badge({ tone = "gray", children, title }: { tone?: keyof typeof TONES | string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone] || TONES.gray}`}>
      {children}
    </span>
  );
}

export function NodeChip({ node, small }: { node: string; small?: boolean }) {
  const m = NODE_META[node as NodeId];
  if (!m) return <span className="mono text-xs">{node}</span>;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full bg-slate-50 ring-1 ring-slate-200 ${small ? "px-1.5 text-[11px]" : "px-2 py-0.5 text-xs"} font-medium text-slate-700`}>
      <span className="h-2 w-2 rounded-full" style={{ background: m.color }} />
      {m.label}
    </span>
  );
}

export function Button({ children, onClick, kind = "secondary", disabled, type = "button", small, title }: {
  children: ReactNode; onClick?: () => void; kind?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean; type?: "button" | "submit"; small?: boolean; title?: string;
}) {
  const styles = {
    primary: "bg-slate-900 text-white hover:bg-slate-700",
    secondary: "bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50",
    danger: "bg-red-600 text-white hover:bg-red-700",
    ghost: "text-slate-600 hover:bg-slate-100",
  }[kind];
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${small ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm"} ${styles}`}>
      {children}
    </button>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "red" | "amber" }) {
  const color = tone === "red" ? "text-red-700" : tone === "amber" ? "text-amber-700" : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{children}</div>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-16" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} rounded-xl bg-white shadow-xl`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h3 className="font-semibold text-slate-800">{title}</h3>
          <button onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

const inputCls = "w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100";
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}
export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={inputCls} />;
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea rows={3} {...props} className={inputCls} />;
}
export function Select({ options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { options: (string | [string, string])[] }) {
  return (
    <select {...props} className={inputCls}>
      {options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o.replace(/_/g, " ")];
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

// ---------------------------------------------------------------- mutation helper
export function useAction() {
  const { toast, refresh } = useApp();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try {
      const r = await fn();
      if (ok) toast(ok, "ok");
      refresh();
      return r;
    } catch (e: any) {
      toast(e.message, "error");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [toast, refresh]);
  return { run, busy };
}

// ---------------------------------------------------------------- shared domain bits
export const PRIORITY_TONE: Record<string, string> = { critical: "red", high: "amber", medium: "blue", low: "gray" };
export const STATUS_TONE: Record<string, string> = {
  open: "blue", in_progress: "violet", fulfilled: "green", cancelled: "gray", assigned: "blue", completed: "green",
  full: "amber", closed: "gray", available: "green", allocated: "amber", unavailable: "gray", exhausted: "red",
  reserved: "amber", distributed: "green", released: "gray", pending: "amber", declined: "gray",
  missing: "red", needs_assistance: "amber", located: "blue", found: "green", reunited: "green", unknown: "gray",
};

export function DisputedBadge({ fields }: { fields?: string[] }) {
  if (!fields || !fields.length) return null;
  return <Badge tone="red" title="Concurrent offline updates disagree; see the Conflict Center">disputed: {fields.join(", ")}</Badge>;
}

export function HistoryModal({ entityType, entityId, title, onClose }: { entityType: string; entityId: string; title: string; onClose: () => void }) {
  const { node } = useApp();
  const { data, error } = usePoll(() => get<any[]>(node, `/api/history/${entityType}/${entityId}`), [node, entityId], 0);
  return (
    <Modal title={`History: ${title}`} onClose={onClose} wide>
      <p className="mb-3 text-xs text-slate-500">
        Every change is an immutable signed event. This is the event log for this record as known by <NodeChip node={node} />,
        ordered by Lamport clock (causal order).
      </p>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <ol className="space-y-2">
        {(data || []).map((h) => (
          <li key={h.event_id} className="rounded-lg border border-slate-200 p-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <NodeChip node={h.origin} small />
              <span className="mono text-xs text-slate-500">L{h.lamport} · {h.origin}#{h.seq}</span>
              <span className="font-medium text-slate-800">{h.type}</span>
              <span className="ml-auto text-xs text-slate-500">{new Date(h.created_at).toLocaleString()}</span>
            </div>
            <pre className="mono mt-1.5 overflow-x-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-[11px] text-slate-700">{JSON.stringify(h.payload, null, 1)}</pre>
            {h.rejected && <div className="mt-1 text-xs font-medium text-red-700">Rejected by validation: {h.rejected}</div>}
          </li>
        ))}
      </ol>
    </Modal>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
            {head.map((h, i) => <th key={i} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Toasts({ items }: { items: { id: number; msg: string; kind: string }[] }) {
  return (
    <div className="fixed bottom-4 right-4 z-[60] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
      {items.map((t) => (
        <div key={t.id} role="status" className={`rounded-lg px-3 py-2 text-sm shadow-lg ring-1 ${t.kind === "error" ? "bg-red-50 text-red-900 ring-red-200" : t.kind === "ok" ? "bg-emerald-50 text-emerald-900 ring-emerald-200" : "bg-white text-slate-800 ring-slate-200"}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}
