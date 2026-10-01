import { useState, type FormEvent } from "react";
import { SOURCE_LABELS, api, money, type Contact } from "./api";

export function Contacts({ contacts }: { contacts: Contact[] }) {
  return (
    <div className="card table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Company</th>
            <th>Email</th>
            <th>Phone</th>
            <th>Status</th>
            <th className="num">Deals</th>
            <th className="num">Open pipeline</th>
            <th>Added by</th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((c) => (
            <tr key={c.id}>
              <td className="strong">{c.full_name}</td>
              <td>{c.company ?? "—"}</td>
              <td>{c.email ?? "—"}</td>
              <td>{c.phone ?? "—"}</td>
              <td>
                <span className={`badge status-${c.status}`}>{c.status === "customer" ? "Customer" : "Lead"}</span>
              </td>
              <td className="num">{c.deals}</td>
              <td className="num">{money(c.open_value)}</td>
              <td className="muted">{SOURCE_LABELS[c.source]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const EMPTY = { full_name: "", company: "", email: "", phone: "", deal_title: "", deal_value: "" };

// Creating a lead here runs the same lead-intake automation as the voice agent
// and the inbound webhook: a deal in "New" plus an intro-call task for tomorrow.
export function LeadForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/api/contacts", {
        ...form,
        deal_value: form.deal_value ? Number(form.deal_value) : null,
      });
      setForm(EMPTY);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the lead");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="card lead-form" onSubmit={submit}>
      <h3>New lead</h3>
      <div className="grid">
        <label>
          Full name*
          <input value={form.full_name} onChange={set("full_name")} required minLength={2} maxLength={120} />
        </label>
        <label>
          Company
          <input value={form.company} onChange={set("company")} maxLength={120} />
        </label>
        <label>
          Email
          <input type="email" value={form.email} onChange={set("email")} maxLength={200} />
        </label>
        <label>
          Phone
          <input value={form.phone} onChange={set("phone")} maxLength={40} />
        </label>
        <label>
          Deal
          <input value={form.deal_title} onChange={set("deal_title")} maxLength={160} placeholder="e.g. Website redesign" />
        </label>
        <label>
          Value (USD)
          <input type="number" min={0} step="any" value={form.deal_value} onChange={set("deal_value")} />
        </label>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="form-actions">
        <button className="btn ghost" type="button" onClick={onDone}>
          Cancel
        </button>
        <button className="btn primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : "Add lead"}
        </button>
      </div>
    </form>
  );
}
