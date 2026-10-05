import { useEffect, useRef, useState } from "react";
import { getSources, verify } from "./api.js";
import ResultCard from "./components/ResultCard.jsx";

const SAMPLE =
  "صباح الخير يا أحبة 🌸\nقال رسول الله ﷺ: «إنما الأعمال بالنيات»\nوقال أيضاً: «من نشر هذه الرسالة فتح الله له أبواب الرزق»\nانشرها تؤجر!";

function countLabel(n) {
  if (n === 1) return "وجدنا نصاً واحداً في المنشور.";
  if (n === 2) return "وجدنا نصّين في المنشور.";
  if (n <= 10) return `وجدنا ${n} نصوص في المنشور.`;
  return `وجدنا ${n} نصاً في المنشور.`;
}

function readSpecialist() {
  try {
    return localStorage.getItem("muthbit:specialist") === "1";
  } catch {
    return false;
  }
}

export default function App() {
  const [specialist, setSpecialist] = useState(readSpecialist);
  const [text, setText] = useState("");
  const [state, setState] = useState({ status: "idle" });
  const [sources, setSources] = useState([]);
  const resultsRef = useRef(null);

  useEffect(() => {
    getSources().then(setSources).catch(() => {});
  }, []);

  function toggleSpecialist() {
    const next = !specialist;
    setSpecialist(next);
    try {
      localStorage.setItem("muthbit:specialist", next ? "1" : "0");
    } catch {
      /* التخزين غير متاح: يبقى الاختيار لهذه الجلسة */
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setState({ status: "loading" });
    try {
      const data = await verify(text);
      setState({ status: "done", data });
      requestAnimationFrame(() => resultsRef.current?.focus());
    } catch (err) {
      setState({ status: "error", message: err.message });
    }
  }

  const results = state.data?.results || [];

  return (
    <div className="page">
      <header className="masthead">
        <div className="masthead-top">
          <h1 className="wordmark">مُثبِت</h1>
          <label className="switch">
            <input type="checkbox" checked={specialist} onChange={toggleSpecialist} />
            <span>وضع المتخصص</span>
          </label>
        </div>
        <p className="lede">
          الصق منشوراً فيه حديث أو آية أو قول منسوب، وسنبيّن لك مصدر كل نص وحكمه كما نقله أهل الاختصاص.
        </p>
      </header>

      <form className="composer" onSubmit={onSubmit}>
        <label htmlFor="post" className="composer-label">النص المتداول</label>
        <textarea
          id="post"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="الصق هنا المنشور كما وصلك…"
          rows={6}
          maxLength={4000}
        />
        <div className="composer-actions">
          <button type="submit" className="primary" disabled={state.status === "loading" || !text.trim()}>
            {state.status === "loading" ? "جارٍ التحقق…" : "تحقّق من النصوص"}
          </button>
          {!text && (
            <button type="button" className="link" onClick={() => setText(SAMPLE)}>
              جرّب منشوراً نموذجياً
            </button>
          )}
          <span className="counter">{text.length} / 4000</span>
        </div>
      </form>

      <section className="results" ref={resultsRef} tabIndex={-1} aria-live="polite">
        {state.status === "error" && <p className="notice error">{state.message}</p>}
        {state.status === "done" && !state.data.index_ready && (
          <p className="notice">لم تُحمّل قاعدة المصادر بعد، لذلك ستظهر كل النتائج امتناعاً. شغّل أمر بناء الفهرس على الخادم.</p>
        )}
        {state.status === "done" && (
          <>
            <p className="summary">
              {countLabel(results.length)}
            </p>
            {results.map((r, i) => (
              <ResultCard key={i} result={r} specialist={specialist} />
            ))}
            <p className="disclaimer">{state.data.disclaimer}</p>
          </>
        )}
      </section>

      <footer className="colophon">
        <h2>المصادر المعتمدة في الأداة</h2>
        {sources.length === 0 ? (
          <p>لا تتوفر قائمة المصادر حالياً.</p>
        ) : (
          <ul>
            {sources.map((s) => (
              <li key={s.name + s.edition}>
                {s.name}
                {s.edition && <span className="muted">، {s.edition}</span>}
                <span className="muted">، عدد النصوص: {s.count}</span>
              </li>
            ))}
          </ul>
        )}
      </footer>
    </div>
  );
}
