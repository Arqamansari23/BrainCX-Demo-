import { STAGE_LABELS, money, shortDate, type Board, type Deal, type Stage } from "./api";

type Props = {
  board: Board;
  flash: Set<number>;
  onChangeStage: (deal: Deal, stage: Stage) => void;
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
  onChangeStage: (deal: Deal, stage: Stage) => void;
}) {
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
          value={deal.stage}
          onChange={(e) => onChangeStage(deal, e.target.value as Stage)}
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
