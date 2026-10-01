import { useEffect, useRef, useState } from "react";
import { STAGE_LABELS, money, shortDate, type Board, type Deal, type Stage } from "./api";

// Resolves true when the server accepted the change.
type ChangeStage = (deal: Deal, stage: Stage) => Promise<boolean>;

type Props = {
  board: Board;
  flash: Set<number>;
  onChangeStage: ChangeStage;
};

export function PipelineBoard({ board, flash, onChangeStage }: Props) {
  return (
    <div className="board">
      {board.stages.map((stage) => {
        const deals = board.deals.filter((d) => d.stage === stage);
        const total = deals.reduce((sum, d) => sum + d.value, 0);
        return (
          <div key={stage} className={`column stage-${stage}`}>
            <div className="column-head">
              <span className="stage-name">{STAGE_LABELS[stage]}</span>
              <span className="pill">{deals.length}</span>
              <span className="column-total">{money(total)}</span>
            </div>
            {deals.map((deal) => (
              <DealCard
                key={deal.id}
                deal={deal}
                stages={board.stages}
                today={board.today}
                flash={flash.has(deal.id)}
                onChangeStage={onChangeStage}
              />
            ))}
            {deals.length === 0 && <p className="empty">No deals</p>}
          </div>
        );
      })}
    </div>
  );
}

// Arrow keys on a closed <select> fire "change" at every step in Chrome and Edge,
// so wait until the choice settles before saving. Otherwise arrowing from
// Contacted to Won would run every stage's automation on the way.
const SETTLE_MS = 600;

function DealCard({
  deal,
  stages,
  today,
  flash,
  onChangeStage,
}: {
  deal: Deal;
  stages: Stage[];
  today: string;
  flash: boolean;
  onChangeStage: ChangeStage;
}) {
  const [pending, setPending] = useState<Stage | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => () => clearTimeout(timer.current), []);
  // Once the server's copy of the stage changes, show that again.
  useEffect(() => setPending(null), [deal.stage]);

  const choose = (stage: Stage) => {
    setPending(stage);
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      if (stage === deal.stage) {
        setPending(null);
        return;
      }
      if (!(await onChangeStage(deal, stage))) setPending(null);
    }, SETTLE_MS);
  };

  const overdue = deal.next_task_due !== null && deal.next_task_due < today;
  return (
    <article className={`card deal ${flash ? "flash" : ""}`}>
      <div className="deal-title">{deal.title}</div>
      <div className="deal-contact">
        {deal.contact}
        {deal.company && <span className="muted"> · {deal.company}</span>}
      </div>
      <div className="deal-row">
        <span className="deal-value">{money(deal.value)}</span>
        <select
          value={pending ?? deal.stage}
          onChange={(e) => choose(e.target.value as Stage)}
          aria-label={`Stage for ${deal.title}`}
        >
          {stages.map((s) => (
            <option key={s} value={s}>
              {STAGE_LABELS[s]}
            </option>
          ))}
        </select>
      </div>
      {deal.next_task && deal.next_task_due && (
        <div className={`next-task ${overdue ? "overdue" : ""}`}>
          Next: {deal.next_task} · {shortDate(deal.next_task_due)}
        </div>
      )}
    </article>
  );
}
