import type { ReviewView } from "../api/engineeringClient";

export function ReviewQueuePanel({ reviews }: { reviews: ReviewView[] }) {
  return <section className="ide-document-section" data-component="v63-review-queue" aria-label="Review Queue">
    <h3>Review Queue</h3>
    {reviews.length === 0 ? <p className="ide-list-empty">暂无待审核项目。</p> : <div className="ide-history-list">
      {reviews.map((review) => <article key={review.review_id} className="ide-context-count">
        <span>{review.target_type} · {review.target_id}</span>
        <strong>{review.status}</strong>
      </article>)}
    </div>}
  </section>;
}
