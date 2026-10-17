import type { EngineeringReviewSnapshot } from "../../types/engineering";

export function ReviewPanel({ reviews }: { reviews: EngineeringReviewSnapshot[] }) {
  return <section className="panel"><div className="panel-heading"><h2>Engineering review</h2><span className="status-pill status-pending">Projection</span></div><div className="review-list">{reviews.map((review) => <article key={review.review_id}><div className="panel-heading"><strong>{review.category}</strong><span>{review.status}</span></div><p>{review.finding_reference}</p><p className="muted">Confidence: {review.confidence}</p></article>)}</div></section>;
}
