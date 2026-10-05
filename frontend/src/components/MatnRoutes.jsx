const TONE = {
  confirm: "tone-confirm",
  confirm_diff_wording: "tone-wording",
  daif: "tone-daif",
  mawdu: "tone-mawdu",
  abstain: "tone-abstain",
  conflict: "tone-conflict",
  misattributed: "tone-attrib",
  isnad: "tone-isnad",
  narrated: "tone-narrated",
};

/* كل حديث (صحابي) ورد فيه المتن، بحكمه وعلاقته بالنص المتداول، ليعرف الباحث حكم المتن من طرقه كلها. */
export default function MatnRoutes({ routes, summary }) {
  if (!routes || routes.length < 2) return null;
  return (
    <section className="matn-routes" aria-label="طرق المتن وأحكامها">
      <h4>طرق المتن وأحكامها</h4>
      {summary && <p className="matn-summary">{summary}</p>}
      <ol>
        {routes.map((r, i) => (
          <li key={i} className={`matn-route ${TONE[r.verdict] || ""}`}>
            <div className="matn-route-head">
              <strong>حديث {r.rawi}</strong>
              <span className="book-tag">{r.relation}</span>
              {r.is_primary && <span className="book-tag">المعروض أعلاه</span>}
            </div>
            <p className="matn-route-verdict">
              {r.verdict_label}
              {r.summary && <span>: {r.summary}</span>}
            </p>
            <p className="original small">{r.text}</p>
            {r.same_hadith_texts?.length > 0 && (
              <details className="alternatives">
                <summary>روايات أخرى للحديث نفسه ({r.same_hadith_texts.length})</summary>
                {r.same_hadith_texts.map((t, j) => (
                  <p key={j} className="original small">{t}</p>
                ))}
              </details>
            )}
            {r.grades?.length > 0 && (
              <details className="alternatives">
                <summary>أحكام المحدّثين ({r.grades.length})</summary>
                <ul className="grades">
                  {r.grades.map((g, j) => (
                    <li key={j}>
                      {g.grade}
                      {g.grader && <span className="muted">، {g.grader}</span>}
                      {g.ref && <span className="muted"> ({g.ref})</span>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
