import { useState } from "react";
import { reportError } from "../api.js";
import IsnadPanel from "./IsnadPanel.jsx";
import MatnRoutes from "./MatnRoutes.jsx";

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

function Takhrij({ m }) {
  const where = m.kind === "quran" ? m.number : `${m.source}${m.number ? `، رقم ${m.number}` : ""}`;
  return (
    <dl className="takhrij">
      <div>
        <dt>{m.kind === "quran" ? "الموضع" : "المصدر"}</dt>
        <dd>
          {where}
          {m.edition && <span className="muted"> ({m.edition})</span>}
        </dd>
      </div>
      {m.rawi && (
        <div>
          <dt>الصحابي</dt>
          <dd>{m.rawi}</dd>
        </div>
      )}
      {m.chapter && (
        <div>
          <dt>الموضع</dt>
          <dd>{m.chapter}</dd>
        </div>
      )}
      {m.kind !== "quran" && m.grade && (
        <div>
          <dt>الحكم</dt>
          <dd>
            {m.grade}
            {m.grader && <span className="muted">، {m.grader}</span>}
            {m.grade_ref && <span className="muted"> ({m.grade_ref})</span>}
          </dd>
        </div>
      )}
      {m.other_grades?.length > 0 && (
        <div>
          <dt>أحكام أخرى</dt>
          <dd>
            <ul className="grades">
              {m.other_grades.map((g, i) => (
                <li key={i}>
                  {g.grade}
                  {g.grader && <span className="muted">، {g.grader}</span>}
                  {g.ref && <span className="muted"> ({g.ref})</span>}
                </li>
              ))}
            </ul>
          </dd>
        </div>
      )}
      {m.actual_attribution && (
        <div>
          <dt>النسبة الصحيحة</dt>
          <dd>{m.actual_attribution}</dd>
        </div>
      )}
      {m.grades_via && (
        <div>
          <dt>الأحكام من</dt>
          <dd>
            <a href={m.grades_url} target="_blank" rel="noreferrer">{m.grades_via}</a>
          </dd>
        </div>
      )}
      {m.via && (
        <div>
          <dt>البحث عبر</dt>
          <dd>{m.via}</dd>
        </div>
      )}
      {m.url && (
        <div>
          <dt>للتحقق</dt>
          <dd>
            <a href={m.url} target="_blank" rel="noreferrer">
              {m.via ? "افتح نتائج البحث في الدرر السنية" : "افتح المصدر"}
            </a>
          </dd>
        </div>
      )}
    </dl>
  );
}

function ReportForm({ result, onDone }) {
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState("idle");
  async function send(e) {
    e.preventDefault();
    setStatus("sending");
    try {
      await reportError({
        claim: result.claim,
        verdict: result.verdict,
        matched_text: result.match?.id ?? null,
        comment,
      });
      setStatus("sent");
    } catch {
      setStatus("failed");
    }
  }
  if (status === "sent") return <p className="report-done">أُرسل البلاغ، وسيراجعه المختص.</p>;
  return (
    <form className="report" onSubmit={send}>
      <label htmlFor={`r-${result.claim}`}>ما الخطأ في هذه النتيجة؟</label>
      <textarea id={`r-${result.claim}`} rows={2} value={comment} onChange={(e) => setComment(e.target.value)}
        placeholder="مثال: الحكم المذكور لا يوافق المصدر، أو النص موجود في كتاب آخر" />
      <div className="report-actions">
        <button type="submit" className="secondary" disabled={status === "sending"}>
          {status === "sending" ? "جارٍ الإرسال…" : "أرسل البلاغ"}
        </button>
        <button type="button" className="link" onClick={onDone}>إلغاء</button>
        {status === "failed" && <span className="error-inline">تعذّر الإرسال، أعد المحاولة.</span>}
      </div>
    </form>
  );
}

export default function ResultCard({ result, specialist }) {
  const [reporting, setReporting] = useState(false);
  const [showIsnad, setShowIsnad] = useState(false);
  const m = result.match;
  const isnadId = m?.has_isnad ? m.id : result.local_candidate?.id;
  return (
    <article className={`result ${TONE[result.verdict] || ""}`}>
      <blockquote className="circulated">
        <span className="circulated-label">النص المتداول</span>
        {result.claim}
      </blockquote>

      <h3 className="verdict">{result.verdict_label}</h3>
      {result.summary && <p className="summary-line">{result.summary}</p>}
      <p className="explanation">{result.explanation}</p>
      {result.notes?.map((n, i) => (
        <p key={i} className="note">{n}</p>
      ))}

      {m && (
        <>
          <p className="original" lang="ar">{m.original_text}</p>
          {m.same_hadith_texts?.length > 0 && (
            <details className="alternatives">
              <summary>الأحكام تشمل روايات أخرى للحديث نفسه ({m.same_hadith_texts.length})</summary>
              {m.same_hadith_texts.map((t, i) => (
                <p key={i} className="original small">{t}</p>
              ))}
            </details>
          )}
          <Takhrij m={m} />
        </>
      )}

      <MatnRoutes routes={result.matn_routes} summary={result.matn_summary} />

      {result.alternatives?.length > 0 && (
        <details className="alternatives">
          <summary>نصوص قريبة أخرى ({result.alternatives.length})</summary>
          {result.alternatives.map((a) => (
            <div key={a.id} className="alt">
              <p className="original small">{a.original_text}</p>
              <Takhrij m={a} />
            </div>
          ))}
        </details>
      )}

      {specialist && isnadId && (
        <div className="specialist-bar">
          <button type="button" className="secondary" aria-expanded={showIsnad} onClick={() => setShowIsnad(!showIsnad)}>
            {showIsnad ? "إخفاء الأسانيد" : "الأسانيد ودرجات الرواة"}
          </button>
          {!m?.has_isnad && result.local_candidate && (
            <span className="muted small">
              أقرب رواية في الكتب التسعة: {result.local_candidate.book}، رقم {result.local_candidate.number}
            </span>
          )}
        </div>
      )}
      {specialist && showIsnad && isnadId && <IsnadPanel textId={isnadId} />}

      <div className="card-foot">
        {!reporting && (
          <button type="button" className="link" onClick={() => setReporting(true)}>
            أبلغ عن خطأ في هذه النتيجة
          </button>
        )}
      </div>
      {reporting && <ReportForm result={result} onDone={() => setReporting(false)} />}
    </article>
  );
}
