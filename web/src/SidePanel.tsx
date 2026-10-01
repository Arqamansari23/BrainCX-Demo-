import { SOURCE_ICONS, SOURCE_LABELS, shortDate, timeAgo, type Board, type Task } from "./api";

// Follow-ups and the activity feed; rendered in the right column under the voice panel.
export function SidePanel({ board, onComplete }: { board: Board; onComplete: (task: Task) => void }) {
  return (
    <>
      <section className="card panel">
        <h3>
          Follow-ups <span className="pill">{board.tasks.length}</span>
        </h3>
        <ul className="tasks">
          {board.tasks.map((task) => {
            const when =
              task.due_date < board.today ? "overdue" : task.due_date === board.today ? "today" : "";
            return (
              <li key={task.id} className={`task ${when}`}>
                <input
                  type="checkbox"
                  // Stays unticked until the refreshed list drops the task, so a
                  // failed save never leaves a ticked box on an open task.
                  checked={false}
                  onChange={() => onComplete(task)}
                  aria-label={`Mark "${task.title}" done`}
                />
                <div className="task-body">
                  <div className="task-title">{task.title}</div>
                  <div className="task-meta">
                    {task.contact} · {when === "overdue" && "Overdue, "}
                    {when === "today" && "Today, "}
                    {shortDate(task.due_date)}
                  </div>
                </div>
                <span className="source-icon" title={`Created by: ${SOURCE_LABELS[task.source]}`}>
                  {SOURCE_ICONS[task.source]}
                </span>
              </li>
            );
          })}
          {board.tasks.length === 0 && <li className="empty">Nothing due. Nice.</li>}
        </ul>
      </section>

      <section className="card panel">
        <h3>Activity</h3>
        <ul className="feed">
          {board.activities.map((a) => (
            <li key={a.id}>
              <span className={`badge src-${a.source}`}>{SOURCE_LABELS[a.source]}</span>
              <div>
                <div>{a.message}</div>
                <div className="muted small">{timeAgo(a.created_at)}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
